"""What the model is asked for when balancing, and what it may answer.

Separated from the generation loop because the two change for different reasons:
the wording and the answer schema are tuned against a model, the loop against a
budget. Everything here is pure — no I/O, no LLM call.
"""

from __future__ import annotations

import json
from typing import Annotated

from pydantic import BaseModel, Field, StringConstraints

from .fields import TextField, read_values, write_values

# Bounded per value: a model that runs away must not write a megabyte into one cell.
Value = Annotated[str, StringConstraints(max_length=2000)]


class BalanceItem(BaseModel):
    """One generated row: the cell of each field, in the order the fields were given.

    Positional rather than keyed by column name, because a column is called
    ``properties.cclom:general_description`` here — a JSON key a small model garbles.
    Each value is a CELL, not a list: a multi-valued field arrives separated the way
    the dataset stores it, which is also the way the examples showed it.
    """

    values: list[Value] = Field(default_factory=list, max_length=50)


class BalanceBatch(BaseModel):
    """What one generation call returns."""

    items: list[BalanceItem] = Field(default_factory=list, max_length=50)


# Bounds one example cell in the prompt. A dataset cell can hold a whole article,
# and a handful of those would cost more per batch than the generation itself.
_EXAMPLE_CHARS = 400

_BALANCE_PROMPT = """Kontext: Ein Katalog für BILDUNGSINHALTE (Lernmaterialien für \
Unterricht und Selbstlernen). Jeder Eintrag beschreibt EIN konkretes Lernmaterial, \
das zu "{label}" gehört.

Felder eines Eintrags, genau in dieser Reihenfolge:
{field_lines}

Echte Einträge aus dem Datensatz als Stil- und Längenvorbild (NICHT kopieren):
{examples}

Erzeuge {n} NEUE, DEUTLICH VERSCHIEDENE Einträge zu "{label}".

Regeln:
- Decke unterschiedliche Teilgebiete von "{label}" ab, nicht nur das Naheliegendste.
- Orientiere dich an Länge und Ton der Beispiele.
- Vermeide diese bereits vorhandenen Titel: {avoid}
- KEINE Personennamen, E-Mail-Adressen, Telefonnummern, URLs, Anbieter- oder \
Institutionsnamen.
Antworte NUR mit JSON: {{"items": [{{"values": [{value_slots}]}}]}}"""


def _field_line(index: int, field: TextField) -> str:
    parts = [f"{index}. {field.column}"]
    if field.guidance:
        parts.append(f" — {field.guidance}")
    if field.separator:
        parts.append(f' (mehrere Werte in EINEM String, getrennt durch "{field.separator}")')
    return "".join(parts)


def build_balance_prompt(
    label: str,
    examples: list[dict],
    fields: list[TextField],
    *,
    n: int,
    avoid_titles: list[str],
) -> str:
    """The generation prompt for one batch of ``label``.

    ``examples`` are real rows as ``{column: cell}``; they are rendered in the SAME
    shape as the requested answer, because a prompt that shows one format and asks
    for another gets both blended (found in the api_v3 generation experiment).
    ``avoid_titles`` are values of the FIRST field already present or produced — the
    block that keeps a batch from collapsing into variants of one item.
    """
    if not examples:
        raise ValueError(f"Label {label!r} has no examples to generate from.")

    rendered = "\n".join(
        json.dumps(
            [write_values(read_values(row.get(f.column), f), f)[:_EXAMPLE_CHARS]
             for f in fields],
            ensure_ascii=False,
        )
        for row in examples
    )
    return _BALANCE_PROMPT.format(
        label=label,
        field_lines="\n".join(_field_line(i, f) for i, f in enumerate(fields, start=1)),
        examples=rendered,
        n=n,
        avoid="; ".join(avoid_titles[-15:]) or "—",
        value_slots=", ".join('"..."' for _ in fields),
    )
