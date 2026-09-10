"""What counts as a key over a set of columns.

Shared by the two operations that match rows by identity rather than by text:
duplicate detection and joining. They must agree on what a key IS, or a
duplicate report would contradict the join that follows it.

**A key is usable only when every part is filled.** One missing component makes
it incomplete, and two incomplete keys are not evidence of the same record.
Without that rule, keying on (url, source) would merge every row that merely
shares a source and lacks a URL -- silent data loss, and the failure mode is
invisible until someone counts the rows.
"""

from __future__ import annotations

import pandas as pd

# ASCII unit separator: cannot occur in a CSV cell, so ("a", "b|c") and
# ("a|b", "c") stay different keys.
KEY_SEPARATOR = "\x1f"


def require_keys(df: pd.DataFrame, keys: list[str], *, what: str = "key column") -> None:
    """Raise a client-safe ``ValueError`` unless every key column exists."""
    if not keys:
        raise ValueError("Choose at least one key column.")
    unknown = [key for key in keys if key not in df.columns]
    if unknown:
        raise ValueError(
            f"Unknown {what}: {', '.join(map(repr, unknown))}. "
            f"Available: {', '.join(map(str, df.columns))}."
        )


def key_series(df: pd.DataFrame, keys: list[str]) -> tuple[pd.Series, pd.Series]:
    """The joined key per row, and a mask of the rows whose key is usable.

    The second value is what callers must respect: rows outside it carry an
    incomplete key and may never be matched against anything.
    """
    parts = [df[key].fillna("").astype(str).str.strip() for key in keys]
    joined = parts[0]
    for part in parts[1:]:
        joined = joined + KEY_SEPARATOR + part
    usable = parts[0] != ""
    for part in parts[1:]:
        usable &= part != ""
    return joined, usable
