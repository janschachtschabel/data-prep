"""What a balancing prompt learns from the dataset beyond its examples.

The model is shown four rows of one label. Everything else it should know comes from
here: what the label is called (the WLO export stores a URI, and a model asked for
entries about ``…/discipline/460`` learns the subject only from the examples), which
other labels a new row must not read like, and how long an entry of each field
typically is. Pure: no I/O, no model call.
"""

from __future__ import annotations

import math
from collections import Counter

import numpy as np
import pandas as pd

from ..textnorm import split_labels
from .balance_prompt import FieldShape
from .fields import TextField, read_values

# A name in the contrast list is one line of the prompt, at most this long.
_NAME_CHARS = 60
# Fewer filled cells than this and a percentile describes the few rows, not the field.
_MIN_FILLED = 5


def _one_line(text: str) -> str:
    # Dataset text: a line break inside a name would start a new line -- possibly an
    # instruction -- in the prompt.
    return " ".join(text.split())


def display_names(df: pd.DataFrame, label_column: str, separator: str) -> dict[str, str]:
    """Label value -> the name people read, from ``<label_column>_DISPLAYNAME``.

    A cell with one label takes the whole name cell, so a name containing the separator
    ("Politik, Gesellschaft") stays whole. Several labels pair with several names only
    when both split into the same number of parts; otherwise nothing is attributed --
    a wrong name in the prompt is worse than the value. The first pairing wins.
    """
    column = f"{label_column}_DISPLAYNAME"
    if column not in df.columns:
        return {}
    names: dict[str, str] = {}
    for label_cell, name_cell in zip(df[label_column], df[column], strict=True):
        labels = split_labels(label_cell, separator)
        if len(labels) == 1:
            pairs = [(labels[0], " ".join(str(name_cell).split()))] if isinstance(name_cell, str) else []
        else:
            found = split_labels(name_cell, separator)
            pairs = list(zip(labels, found, strict=True)) if len(found) == len(labels) else []
        for label, name in pairs:
            if name:
                names.setdefault(label, name)
    return names


def contrast_labels(
    label: str,
    rows_by_label: dict[str, list[int]],
    labels_of_row: list[list[str]],
    names: dict[str, str],
    *,
    cap: int = 30,
) -> tuple[list[str], int]:
    """The other labels a new row of ``label`` must not read like, as the prompt names
    them, and how many were left out.

    Labels sharing rows with ``label`` come first: a row carrying both is where the two
    blur, so that is where generated text drifts. Then by support -- the labels the
    model sees most. Capped, because every batch pays for every name.
    """
    shared = Counter(other for row in rows_by_label.get(label, [])
                     for other in labels_of_row[row] if other != label)
    others = sorted((other for other in rows_by_label if other != label),
                    key=lambda other: (-shared[other], -len(rows_by_label[other]), other))
    named = [_one_line(names.get(other, other))[:_NAME_CHARS] for other in others[:cap]]
    return named, max(0, len(others) - cap)


def _middle_half(numbers: list[int], step: int) -> tuple[int, int]:
    """The 25th and 75th percentile, rounded to ``step`` -- "40-90 characters" reads as a
    range to aim for, "43-87" as a measurement to hit."""
    low, high = np.percentile(numbers, [25, 75])

    def rounded(value: float) -> int:
        return max(step, math.floor(value / step + 0.5) * step)

    return rounded(low), max(rounded(low), rounded(high))


def field_shapes(
    rows: list[list[str]],
    fields: list[TextField],
    fallback: list[FieldShape | None] | None = None,
) -> list[FieldShape | None]:
    """Per field, how its filled cells in ``rows`` typically look.

    ``rows`` holds each row's normalised cells in field order. A field with fewer than a
    handful of filled cells takes ``fallback``'s shape -- the whole dataset's, for a label
    with three rows -- or none. A list field's number of values never goes below what
    the field requires: the gate would discard such an answer after it was paid for.
    """
    shapes: list[FieldShape | None] = []
    for index, field in enumerate(fields):
        filled = [cells[index] for cells in rows if cells[index]]
        if len(filled) < _MIN_FILLED:
            shapes.append(fallback[index] if fallback is not None else None)
            continue
        values = None
        if field.separator:
            low, high = _middle_half([len(read_values(cell, field)) for cell in filled], 1)
            low = max(low, field.min_values)
            values = (low, max(high, low))
        shapes.append(FieldShape(chars=_middle_half([len(cell) for cell in filled], 10),
                                 values=values))
    return shapes
