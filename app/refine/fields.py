"""What a text field is, and how one cell of it is read and written.

Enrichment used to know three roles — title, description, keywords — and the fact
that keywords are comma-separated lived inside the keyword branch. That made every
further list field (authors, ISO codes, material types) a new branch.

Here the list-ness belongs to the FIELD: ``separator`` set means the cell holds
several values, whichever character separates them. Everything downstream then reads
and writes values instead of strings, and a field nobody anticipated needs no code.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TextField:
    """One text column of a dataset, and how its cell is to be understood.

    ``separator`` ``None`` means one value per cell — a title containing a comma is
    still one title. ``min_values`` is what counts as complete: the shipped keyword
    enrichment has always asked for three, and two is a gap. ``guidance`` is the one
    line a prompt shows for this field, so a new field is described in data rather
    than in a new prompt constant.
    """

    column: str
    separator: str | None = None
    min_values: int = 1
    guidance: str = ""


def read_values(cell: object, field: TextField) -> list[str]:
    """The values in ``cell``, stripped, without the empties an export leaves behind.

    A missing cell arrives from pandas as NaN (a float, not a string), and blank or
    whitespace means the same as absent: no values.
    """
    if not isinstance(cell, str):
        return []
    if field.separator is None:
        stripped = cell.strip()
        return [stripped] if stripped else []
    return [part.strip() for part in cell.split(field.separator) if part.strip()]


def write_values(values: list[str], field: TextField) -> str:
    """``values`` as one cell of ``field``.

    A single-valued field keeps the first value: a model asked for one description
    that returns two must not quietly leave both in the column.
    """
    cleaned = [value.strip() for value in values if value and value.strip()]
    if field.separator is None:
        return cleaned[0] if cleaned else ""
    return f"{field.separator} ".join(cleaned)


def is_gap(cell: object, field: TextField) -> bool:
    """Does this cell hold fewer values than the field asks for?"""
    return len(read_values(cell, field)) < field.min_values
