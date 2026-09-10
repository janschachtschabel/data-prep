"""Duplicates over chosen key columns: report them, or remove them.

Distinct from the dedupe filters in ``filters.py``, which key on the COMBINED
TEXT and answer "is this the same material?". These key on whatever identifies a
row in the source system -- a URL, an id, a pair of columns -- and answer "is
this the same record?".

Reporting and removing live together because they share the key: a report that
counted differently from what the removal does would be worse than no report.

Rows whose key is incomplete are never duplicates of each other -- see
``keys.py`` for why. Their number is reported so the operator can see the key
was a poor choice.
"""

from __future__ import annotations

import pandas as pd

from .keys import KEY_SEPARATOR, key_series, require_keys

_EXAMPLES = 5


def duplicate_report(df: pd.DataFrame, keys: list[str], *, examples: int = _EXAMPLES) -> dict:
    """How many rows share a key, and which keys repeat.

    Non-destructive. ``removable_rows`` is what :func:`dedupe_keys` would drop:
    one row per group survives.

    Raises ``ValueError`` (client-safe) for an unknown or missing key column.
    """
    require_keys(df, keys)
    joined, has_key = key_series(df, keys)
    keyed = joined[has_key]

    counts = keyed.value_counts()
    repeated = counts[counts > 1]
    return {
        "keys": list(keys),
        "rows": int(len(df)),
        "unique_keys": int(counts.size),
        "empty_key_rows": int((~has_key).sum()),
        "duplicate_groups": int(repeated.size),
        "duplicate_rows": int(repeated.sum()),
        "removable_rows": int(repeated.sum() - repeated.size),
        "examples": [
            {
                "key": dict(zip(keys, str(value).split(KEY_SEPARATOR), strict=False)),
                "count": int(count),
            }
            for value, count in repeated.head(max(examples, 0)).items()
        ],
    }


def dedupe_keys(df: pd.DataFrame, params: dict, ctx: dict) -> tuple[pd.DataFrame, dict]:
    """Pipeline operation: keep one row per key in ``params['keys']``.

    ``params['keep']`` is ``"first"`` (default) or ``"last"`` -- sources are
    often ordered oldest-first, so "last" is how the newest record wins.

    Same shape as every other operation, never mutating the input. ``ctx`` is
    unused: a key is any column, not a label.
    """
    keys = list(params.get("keys") or [])
    require_keys(df, keys)
    keep = params.get("keep", "first")
    if keep not in ("first", "last"):
        raise ValueError(f"Unknown keep mode {keep!r}. Use 'first' or 'last'.")

    joined, has_key = key_series(df, keys)
    # Only KEYED rows can be duplicates; a row without a key always survives.
    is_duplicate = joined.duplicated(keep=keep) & has_key
    new = df[~is_duplicate].reset_index(drop=True)

    dropped = df[is_duplicate]
    return new, {
        "filter": "dedupe_keys",
        "before": int(len(df)),
        "after": int(len(new)),
        "removed": int(is_duplicate.sum()),
        "changed": 0,
        "keys": list(keys),
        "empty_key_rows": int((~has_key).sum()),
        "examples": [
            {"removed": " | ".join(map(str, row))[:120]}
            for row in dropped.head(_EXAMPLES).itertuples(index=False)
        ],
    }
