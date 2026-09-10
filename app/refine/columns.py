"""Column operations: keep, drop, rename.

Small on their own, but they are what makes a flattened export workable -- one
WLO record expands to well over a hundred dot-path columns and only a handful
are training material.

Same pipeline shape as every other operation, ``(df, params, ctx) -> (new_df,
stats)``, never mutating the input. ``ctx`` is unused: these know nothing about
text or label columns.

Every rejection is a ``ValueError`` with a client-safe message, so the route
maps it to 400. A bare pandas ``KeyError`` would surface as a 500 and tell the
operator nothing about which name was wrong.
"""

from __future__ import annotations

import pandas as pd


def _available(df: pd.DataFrame) -> str:
    return ", ".join(map(str, df.columns))


def _require_known(df: pd.DataFrame, names: list[str], what: str) -> None:
    unknown = [name for name in names if name not in df.columns]
    if unknown:
        raise ValueError(
            f"Unknown {what}: {', '.join(map(repr, unknown))}. Available: {_available(df)}."
        )


def _duplicates(names: list[str]) -> list[str]:
    seen: set[str] = set()
    repeated: list[str] = []
    for name in names:
        if name in seen:
            repeated.append(name)
        seen.add(name)
    return repeated


def _stats(name: str, df: pd.DataFrame, new: pd.DataFrame, changed: int) -> dict:
    """The shape the operation history stores, shared with the row filters.

    ``removed`` counts ROWS, which a column operation never touches -- keeping
    the key at 0 rather than omitting it lets one history list render both
    kinds of step without special cases.
    """
    return {
        "filter": name,
        "before": int(len(df)),
        "after": int(len(new)),
        "removed": 0,
        "changed": changed,
        "columns_before": list(df.columns),
        "columns_after": list(new.columns),
        "examples": [],
    }


def select_columns(df: pd.DataFrame, params: dict, ctx: dict) -> tuple[pd.DataFrame, dict]:
    """Keep only ``params['keep']``, in the order given.

    The order is deliberate: it becomes the column order of the exported CSV,
    so it is a choice rather than an accident of the source file.
    """
    keep = list(params.get("keep") or [])
    if not keep:
        raise ValueError("Select at least one column to keep.")
    repeated = _duplicates(keep)
    if repeated:
        # pandas would produce two columns of one name and every later lookup
        # would be ambiguous.
        raise ValueError(f"Column named twice: {', '.join(map(repr, repeated))}.")
    _require_known(df, keep, "column")
    new = df[keep].copy()
    return new, _stats("select_columns", df, new, len(df.columns) - len(keep))


def drop_columns(df: pd.DataFrame, params: dict, ctx: dict) -> tuple[pd.DataFrame, dict]:
    """Remove ``params['drop']``, keeping the order of what survives."""
    drop = list(params.get("drop") or [])
    if not drop:
        raise ValueError("Name at least one column to drop.")
    _require_known(df, drop, "column")
    surviving = [column for column in df.columns if column not in set(drop)]
    if not surviving:
        # An empty frame is not a dataset -- the store cannot even list it.
        raise ValueError("Cannot drop every column; at least one has to remain.")
    new = df[surviving].copy()
    return new, _stats("drop_columns", df, new, len(df.columns) - len(surviving))


def rename_columns(df: pd.DataFrame, params: dict, ctx: dict) -> tuple[pd.DataFrame, dict]:
    """Rename by ``params['mapping']`` (old name -> new name), keeping position."""
    mapping = dict(params.get("mapping") or {})
    if not mapping:
        raise ValueError("Provide at least one column to rename.")
    _require_known(df, list(mapping), "column")

    blank = [old for old, new_name in mapping.items() if not str(new_name).strip()]
    if blank:
        raise ValueError(f"New name is empty for: {', '.join(map(repr, blank))}.")

    # A no-op rename (same name in and out) is allowed and must not count as a
    # collision -- it is what a half-edited mapping in the UI looks like.
    effective = {old: str(new).strip() for old, new in mapping.items() if str(new).strip() != old}
    resulting = [effective.get(column, column) for column in df.columns]
    clashes = _duplicates(resulting)
    if clashes:
        raise ValueError(
            f"Name already taken: {', '.join(map(repr, sorted(set(clashes))))}."
        )

    new = df.rename(columns=effective).copy()
    return new, _stats("rename_columns", df, new, len(effective))
