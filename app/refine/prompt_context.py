"""What an LLM prompt learns from the dataset it works on.

Balancing shows the model four rows of one label, enrichment one row. Everything else
the model should know comes from here: what a label is called (the WLO export stores a
URI, and a model asked for entries about ``…/discipline/460`` learns the subject only
from the examples), which other labels a new row must not read like, and how long an
entry of each field typically is. Pure: no I/O, no model call.
"""

from __future__ import annotations

import math
import re
from collections import Counter, defaultdict
from dataclasses import dataclass

import numpy as np
import pandas as pd

from ..textnorm import split_labels
from .fields import TextField, read_values

# The most one answer value may hold. A longer one is turned away where the answer is
# read -- the item it is in (balancing), or the value alone (enrichment) -- not by the
# answer's schema, where it failed the whole answer and the run it was paid in.
MAX_VALUE_CHARS = 2000
# The most a prompt states as a typical length. Not the cap itself: a model asked for
# "1750-2000" aims at the upper end and overshoots it.
MAX_STATED_CHARS = MAX_VALUE_CHARS * 3 // 4
# A name from the dataset -- a label, a display name -- is one line of the prompt, at
# most this long.
NAME_CHARS = 60
# Fewer filled cells than this and a percentile describes the few rows, not the field.
_MIN_FILLED = 5


@dataclass(frozen=True)
class FieldShape:
    """How a field's filled cells typically look in the dataset: the middle half of
    their length in characters and, for a list, of their number of values."""

    chars: tuple[int, int] | None = None
    values: tuple[int, int] | None = None


# What a stated length counts, in every case a German sentence puts it: "2-3 Sätzen",
# "50 Wörtern".
_UNIT = (r"(?:Zeichen|W(?:ö|oe)rtern?|Worte?n?|Werte?n?|S(?:ä|ae)tzen?|Satz|Schlagw\w*"
         r"|Stichw\w*|Begriffe?n?|Keywords?|Tags?|characters?|chars|words?|values?|sentences?)")
_NUMBER_WORD = r"(?:zwei|drei|vier|fünf|sechs|sieben|acht|neun|zehn|elf|zwölf)"
# "ein" counts only what a length is measured in: "in einem Satz" is one, "an einem Tag"
# and "einen Begriff aus dem Lehrplan" are none.
_ONE = r"ein(?:e[mnrs]?)?\s+(?:\w+\s+)?(?:Satz|S(?:ä|ae)tzen?|Worte?n?|W(?:ö|oe)rtern?|Zeichen)"
# A number, a range or a number word, then -- within three words -- what it counts:
# "100-400 Zeichen", "drei treffende deutsche Schlagwörter", "zwei bis drei Sätze".
# "eine Liste von Begriffen" counts nothing, and "m²" is not a number.
_STATED_LENGTH = re.compile(
    rf"\b(?:[0-9]+(?:\s*[-–]\s*[0-9]+)?|{_NUMBER_WORD})\s+(?:\w+\s+){{0,3}}?{_UNIT}\b"
    rf"|\b{_ONE}\b",
    re.IGNORECASE)
# A number that names a grade, a level or an age counts nothing -- "für Klasse 5/6
# geeignete Begriffe", "Klassen fünf und sechs", "ab 6 Jahren" -- so it is taken out
# before the search.
_NUM = rf"(?:[0-9]+|eins|{_NUMBER_WORD})"
_GRADE = re.compile(
    rf"\b(?:Klassen?|Jahrg(?:ang|änge)|\w*stufen?|Level|Niveau|Kapitel|Lektion)\s+{_NUM}"
    rf"(?:\s*(?:[-–/]|bis|und)\s*{_NUM})*"
    rf"|\b{_NUM}(?:\s*(?:[-–/]|bis)\s*{_NUM})?\s+Jahren?\b",
    re.IGNORECASE)


def guidance_states_a_length(field: TextField) -> bool:
    """Whether the field's guidance states a length -- "100-400 Zeichen", "3-6
    Schlagwörter", "in zwei Sätzen". Then it is someone's instruction, and the dataset's
    range beside it would contradict it; the prompt keeps theirs and adds none."""
    return bool(_STATED_LENGTH.search(_GRADE.sub(" ", field.guidance)))


def typical_phrase(field: TextField, shape: FieldShape | None) -> str:
    """``shape`` as a prompt says it -- values for a list, characters otherwise -- or
    nothing when the dataset had too little to say."""
    if shape is None:
        return ""
    if field.separator and shape.values:
        (low, high), unit = shape.values, "Werte"
    elif shape.chars:
        (low, high), unit = shape.chars, "Zeichen"
    else:
        return ""
    return f"im Datensatz meist {f'etwa {low}' if low == high else f'{low}–{high}'} {unit}"


def one_line(text: str, limit: int | None = None) -> str:
    """Dataset text as one line of a prompt, cut to ``limit``: a line break inside a
    name would start a new line -- possibly an instruction -- and an unbounded one is
    paid for in every call."""
    return " ".join(text.split())[:limit]


# A double quote in a name would close the prompt's quotation of it -- „{label}“ -- and
# what followed would read as the prompt's own words.
_QUOTES = str.maketrans(dict.fromkeys("\"„“”«»‟", "'"))


def label_name(label: str, name: str | None = None) -> str:
    """How a prompt names ``label``: by its display ``name`` when it has one, else by
    the value -- one line, bounded, with no double quote. A value too long for the line
    keeps its end: the URIs of one vocabulary differ there (``…/sekundarstufe_1``,
    ``…/sekundarstufe_2``), and cut after the host two labels read the same."""
    if name and (line := one_line(name, NAME_CHARS)):
        return line.translate(_QUOTES)
    value = one_line(str(label)).translate(_QUOTES)
    return value if len(value) <= NAME_CHARS else "…" + value[-(NAME_CHARS - 1):]


def display_names(df: pd.DataFrame, label_column: str, separator: str) -> dict[str, str]:
    """Label value -> the name people read, from ``<label_column>_DISPLAYNAME``.

    Every row votes, and the name most rows give a label wins: one mis-paired row, or
    the name cell a label filter left behind, must not name it in every prompt. A row
    pairs its labels with its names when both split into the same number of parts. A
    row with one label and a name cell of several parts holds one name containing the
    separator ("Politik, Gesellschaft") -- or names left from labels filtered away --
    so its cell counts whole, and only for a label no row pairs. Several labels beside
    another number of names attribute nothing: a wrong name in the prompt is worse than
    the value. Of names given equally often, the first wins.
    """
    column = f"{label_column}_DISPLAYNAME"
    if column not in df.columns:
        return {}
    paired: dict[str, Counter[str]] = defaultdict(Counter)
    whole: dict[str, Counter[str]] = defaultdict(Counter)
    for label_cell, name_cell in zip(df[label_column], df[column], strict=True):
        labels = split_labels(label_cell, separator)
        found = split_labels(name_cell, separator)
        if len(found) == len(labels):
            for label, name in zip(labels, found, strict=True):
                if line := one_line(name, NAME_CHARS):
                    paired[label][line] += 1
        elif len(labels) == 1 and isinstance(name_cell, str) and (
                line := one_line(name_cell, NAME_CHARS)):
            whole[labels[0]][line] += 1
    return {label: (paired.get(label) or whole[label]).most_common(1)[0][0]
            for label in {**whole, **paired}}


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
    model sees most. Capped, because every batch pays for every name. A name is listed
    once, and never the label's own -- two values can share a display name, and a label
    told to stay apart from itself is told nothing.
    """
    own = label_name(label, names.get(label))
    shared = Counter(other for row in rows_by_label.get(label, [])
                     for other in labels_of_row[row] if other != label)
    others = sorted((other for other in rows_by_label if other != label),
                    key=lambda other: (-shared[other], -len(rows_by_label[other]), other))
    named = list(dict.fromkeys(
        name for name in (label_name(other, names.get(other)) for other in others)
        if name and name != own))
    return named[:cap], max(0, len(named) - cap)


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
    the field requires: the gate would discard such an answer after it was paid for. A
    length never goes above ``MAX_STATED_CHARS``: a field whose typical entry starts
    beyond it is stated as about that long, and one whose typical entry is longer than an
    answer may hold gets no length at all. Nor does a list whose typical cell is longer
    than ``MAX_STATED_CHARS`` get its number of values: balancing returns the list as one
    string, and the count would ask for that cell -- its characters, capped, say it instead.
    """
    shapes: list[FieldShape | None] = []
    for index, field in enumerate(fields):
        filled = [cells[index] for cells in rows if cells[index]]
        if len(filled) < _MIN_FILLED:
            shapes.append(fallback[index] if fallback is not None else None)
            continue
        low, high = _middle_half([len(cell) for cell in filled], 10)
        chars: tuple[int, int] | None = None
        if low < MAX_STATED_CHARS:
            chars = (low, min(high, MAX_STATED_CHARS))
        elif low < MAX_VALUE_CHARS:
            chars = (MAX_STATED_CHARS, MAX_STATED_CHARS)
        values = None
        if field.separator and high <= MAX_STATED_CHARS:
            fewest, most = _middle_half([len(read_values(cell, field)) for cell in filled], 1)
            fewest = max(fewest, field.min_values)
            values = (fewest, max(most, fewest))
        shapes.append(FieldShape(chars=chars, values=values))
    return shapes
