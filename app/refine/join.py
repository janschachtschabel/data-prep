"""Matching two datasets row-to-row on one or more keys.

Different from ``combine.py``, which maps columns onto a target schema and
CONCATENATES two sources, resolving text duplicates. This reconciles them: the
"same record in two exports, joined on an id" case.

**The failure mode a join has and a concatenation does not is the row
explosion.** A key repeating three times on the left and four on the right
yields twelve rows for that key alone, so a dirty key can exhaust memory before
anyone sees a result. :func:`key_cardinality` therefore predicts the size
BEFORE any join runs, and :func:`join_datasets` refuses to exceed a ceiling
rather than trying and dying.

Rows whose key is incomplete never match anything -- see ``keys.py``.
"""

from __future__ import annotations

import pandas as pd

from .keys import key_series, require_keys

HOW = ("inner", "left", "right", "outer")

# Deliberately generous: the point is to stop a runaway n-to-m join, not to
# limit legitimate work. 5 million all-string rows is already several GB.
DEFAULT_MAX_ROWS = 5_000_000

# A key column pandas can merge on. Prefixed so it cannot collide with a real
# column, and dropped again before the result is returned.
_LEFT_KEY = "__join_key_left__"
_RIGHT_KEY = "__join_key_right__"


def _key_columns(keys: list[dict]) -> tuple[list[str], list[str]]:
    if not keys:
        raise ValueError("Choose at least one key column.")
    left, right = [], []
    for pair in keys:
        if not isinstance(pair, dict) or not pair.get("left") or not pair.get("right"):
            raise ValueError(
                "Every key needs a 'left' and a 'right' column name, "
                f"got {pair!r}."
            )
        left.append(str(pair["left"]))
        right.append(str(pair["right"]))
    return left, right


def _keyed(df: pd.DataFrame, columns: list[str], side: str) -> tuple[pd.Series, pd.Series]:
    require_keys(df, columns, what=f"{side} key column")
    return key_series(df, columns)


def _relation(left_max: int, right_max: int) -> str:
    left_many = left_max > 1
    right_many = right_max > 1
    if left_many and right_many:
        return "many-to-many"
    if left_many:
        return "many-to-one"
    if right_many:
        return "one-to-many"
    return "one-to-one"


def key_cardinality(left: pd.DataFrame, right: pd.DataFrame, keys: list[dict]) -> dict:
    """How the two key sets relate, and how large a join would be.

    Read this before joining: ``estimated_rows`` is the exact number of rows an
    inner join produces, computed without building it, and ``explodes`` says
    whether that exceeds both inputs.

    Raises ``ValueError`` (client-safe) for a missing or malformed key.
    """
    left_columns, right_columns = _key_columns(keys)
    left_joined, left_usable = _keyed(left, left_columns, "left")
    right_joined, right_usable = _keyed(right, right_columns, "right")

    left_counts = left_joined[left_usable].value_counts()
    right_counts = right_joined[right_usable].value_counts()
    shared = left_counts.index.intersection(right_counts.index)
    # The exact inner-join size: each shared key contributes its cross product.
    estimated = int((left_counts[shared] * right_counts[shared]).sum()) if len(shared) else 0

    return {
        "keys": [{"left": a, "right": b} for a, b in zip(left_columns, right_columns, strict=True)],
        "left_rows": int(len(left)),
        "right_rows": int(len(right)),
        "left_unique_keys": int(left_counts.size),
        "right_unique_keys": int(right_counts.size),
        "left_max_repeat": int(left_counts.max()) if left_counts.size else 0,
        "right_max_repeat": int(right_counts.max()) if right_counts.size else 0,
        "left_unusable_key_rows": int((~left_usable).sum()),
        "right_unusable_key_rows": int((~right_usable).sum()),
        # Rows on each side that find no partner. Reported independently of
        # the join type on purpose: a left join silently discards unmatched
        # RIGHT rows, and that loss should be visible rather than implied.
        "left_unmatched_rows": int(len(left) - int((left_usable & left_joined.isin(shared)).sum())),
        "right_unmatched_rows": int(len(right) - int((right_usable & right_joined.isin(shared)).sum())),
        "matching_keys": int(len(shared)),
        "left_only_keys": int(left_counts.size - len(shared)),
        "right_only_keys": int(right_counts.size - len(shared)),
        "relation": _relation(
            int(left_counts.max()) if left_counts.size else 0,
            int(right_counts.max()) if right_counts.size else 0,
        ),
        "estimated_rows": estimated,
        "explodes": estimated > max(len(left), len(right)),
    }


def _prepare(df: pd.DataFrame, joined: pd.Series, usable: pd.Series, column: str) -> pd.DataFrame:
    """A copy carrying its join key, with unusable keys made unique per row.

    A row-unique placeholder rather than a blank or ``None``: **pandas' merge
    matches missing keys with each other**, unlike SQL. Left as NaN, the two
    rows that merely lack an id would join together and produce a confident
    false match. A distinct placeholder per row and side makes that impossible.
    """
    prepared = df.copy()
    placeholder = pd.Series(
        [f"{column}#{position}" for position in range(len(df))], index=df.index
    )
    prepared[column] = joined.where(usable, other=placeholder)
    return prepared


def join_datasets(
    left: pd.DataFrame,
    right: pd.DataFrame,
    *,
    keys: list[dict],
    how: str = "left",
    suffix: str = "_right",
    coalesce: bool = False,
    max_rows: int = DEFAULT_MAX_ROWS,
) -> tuple[pd.DataFrame, dict]:
    """Join ``right`` onto ``left`` over ``keys``.

    ``coalesce`` fills empty cells of a shared column from the right instead of
    adding a suffixed one -- the usual intent when enriching a dataset.

    Refuses with a ``ValueError`` when the result would exceed ``max_rows``;
    the message carries the predicted size so the caller can pick a better key.
    Neither input is mutated.
    """
    if how not in HOW:
        raise ValueError(f"Unknown join type {how!r}. Use one of: {', '.join(HOW)}.")

    report = key_cardinality(left, right, keys)
    # estimated_rows is the INNER size. The other join types keep their unmatched
    # rows too, and the refusal must name the number the caller would get.
    expected = report["estimated_rows"]
    if how in ("left", "outer"):
        expected += report["left_unmatched_rows"]
    if how in ("right", "outer"):
        expected += report["right_unmatched_rows"]
    if expected > max_rows:
        raise ValueError(
            f"This join would produce about {expected} rows "
            f"(limit {max_rows}). The key repeats up to {report['left_max_repeat']} "
            f"times on the left and {report['right_max_repeat']} on the right — "
            f"add a key column so it identifies a row."
        )

    left_columns, right_columns = _key_columns(keys)
    left_joined, left_usable = _keyed(left, left_columns, "left")
    right_joined, right_usable = _keyed(right, right_columns, "right")

    prepared_left = _prepare(left, left_joined, left_usable, _LEFT_KEY)
    prepared_right = _prepare(right, right_joined, right_usable, _RIGHT_KEY)
    # The right key columns are carried THROUGH the merge and dropped after, not
    # before: a right-only row has no value in the left's key column, and
    # dropping early would leave it with no key at all -- a row nothing can
    # identify, which a second join on the same key would silently lose.
    renamed_keys = {column: f"__right_key_{index}__"
                    for index, column in enumerate(right_columns)}
    prepared_right = prepared_right.rename(columns=renamed_keys)

    collided = [c for c in prepared_right.columns
                if c in prepared_left.columns and c != _RIGHT_KEY]

    merged = prepared_left.merge(
        prepared_right, how=how, left_on=_LEFT_KEY, right_on=_RIGHT_KEY,
        suffixes=("", suffix), indicator="__side__",
    )

    matched = int((merged["__side__"] == "both").sum())
    merged = merged.drop(columns=["__side__", _LEFT_KEY, _RIGHT_KEY])

    # Fill the left key from the right one wherever the merge produced no left
    # value (a right-only row), then discard the right key columns.
    for left_column, right_column in zip(left_columns, right_columns, strict=True):
        carried = renamed_keys[right_column]
        present = merged[left_column].notna() & (merged[left_column] != "")
        merged[left_column] = merged[left_column].where(present, merged[carried])
    merged = merged.drop(columns=list(renamed_keys.values()))

    if coalesce:
        for column in collided:
            other = column + suffix
            if other in merged.columns:
                filled = merged[column].fillna("").astype(str)
                merged[column] = filled.where(filled != "", merged[other].fillna(""))
                merged = merged.drop(columns=[other])

    # An unmatched row leaves NaN behind; the store is all-strings, and every
    # operation downstream compares cells as text.
    merged = merged.fillna("").astype(str)

    return merged, {
        "filter": "join",
        "before": int(len(left)),
        "after": int(len(merged)),
        "removed": max(int(len(left)) - int(len(merged)), 0),
        "changed": int(len(merged.columns) - len(left.columns)),
        "how": how,
        "right_rows": int(len(right)),
        "matched_rows": matched,
        "unmatched_left": report["left_unmatched_rows"],
        "unmatched_right": report["right_unmatched_rows"],
        "collided_columns": collided,
        "relation": report["relation"],
        "examples": [],
    }
