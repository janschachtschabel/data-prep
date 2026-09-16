"""The columns this app writes to say where a row came from.

In one place because two modules WRITE them (balance, enrich) and two READ them
(the split keeps marked rows out of an evaluation, combine carries them along).
A name that drifts between writer and reader is a leak nobody sees — the split
would simply stop recognising generated rows.
"""

from __future__ import annotations

import pandas as pd

# The label a generated row was made for. Empty on real rows.
GENERATED_FOR = "generated_for"
# The label(s) a REAL row was shown to the generator for, as an example. Its
# paraphrases sit in the training data, so the row itself must not be evaluated on.
EXAMPLE_FOR = "example_for"
# The fields enrichment filled in this row.
ENRICHED_FIELDS = "enriched_fields"

MARK_COLUMNS = (GENERATED_FOR, EXAMPLE_FOR, ENRICHED_FIELDS)


def is_marked(cell: object) -> bool:
    """Does this cell carry a mark?

    Blank is no mark, and neither is a missing cell: pandas hands an in-memory
    caller NaN there, and ``str(nan)`` is the non-blank string "nan".
    """
    if cell is None or (not isinstance(cell, str) and pd.isna(cell)):
        return False
    return bool(str(cell).strip())


def marked(df: pd.DataFrame, column: str) -> list[bool]:
    """Per row: does ``column`` carry a mark? A frame without it has none."""
    if column not in df.columns:
        return [False] * len(df)
    return [is_marked(cell) for cell in df[column]]
