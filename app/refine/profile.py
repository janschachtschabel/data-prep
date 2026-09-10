"""What is actually in this table? Per-column statistics for any frame.

``analyze`` reports label support and text lengths, but only once someone has
declared which column is the label. This answers the question that comes first,
on a file nobody has mapped yet: how full is each column, how many distinct
values does it hold, which values dominate, and -- where a column holds numbers
-- what range they cover.

Every number returned is a plain Python type. The profile is serialised
straight to the UI, and a numpy scalar breaks ``json.dumps`` while a NaN
silently becomes a JavaScript ``null``.
"""

from __future__ import annotations

import math

import pandas as pd

# Share of non-empty cells that must parse as numbers before a column is
# summarised numerically. Below this the summary would describe a minority of
# the column; above it, a few stray words should not hide a year range.
_NUMERIC_SHARE = 0.8


def _clean(series: pd.Series) -> pd.Series:
    """Non-empty cells as stripped text. Whitespace is empty to a human, and
    imports produce it."""
    text = series.fillna("").astype(str).str.strip()
    return text[text != ""]


def _number(value: float) -> float | int | None:
    """A numpy scalar as a JSON-safe Python number, or None for NaN."""
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return None
    as_float = float(value)
    return int(as_float) if as_float.is_integer() else round(as_float, 6)


def _numeric_summary(filled: pd.Series) -> dict | None:
    numbers = pd.to_numeric(filled, errors="coerce").dropna()
    if filled.empty or len(numbers) / len(filled) < _NUMERIC_SHARE:
        return None
    return {
        # How many cells could be read -- the honest caveat when a column is
        # numeric apart from a few stray entries.
        "parsed": int(len(numbers)),
        "min": _number(numbers.min()),
        "max": _number(numbers.max()),
        "mean": _number(numbers.mean()),
        "median": _number(numbers.median()),
        "p05": _number(numbers.quantile(0.05)),
        "p95": _number(numbers.quantile(0.95)),
    }


def profile_columns(df: pd.DataFrame, *, top_n: int = 10) -> dict:
    """Fill rate, cardinality, common values and numeric range, per column.

    Columns keep their source order, because that is how the operator sees them
    in the viewer and in the exported file.
    """
    columns = []
    for name in df.columns:
        filled = _clean(df[name])
        counts = filled.value_counts().head(max(top_n, 0))
        numeric = _numeric_summary(filled)
        columns.append({
            "name": str(name),
            "filled": int(len(filled)),
            "empty": int(len(df) - len(filled)),
            "fill_rate": round(len(filled) / len(df), 4) if len(df) else 0.0,
            "distinct": int(filled.nunique()),
            "kind": "empty" if filled.empty else ("numeric" if numeric else "text"),
            "top_values": [
                {"value": str(value), "count": int(count)}
                for value, count in counts.items()
            ],
            "numeric": numeric,
        })
    return {"rows": int(len(df)), "columns": columns}
