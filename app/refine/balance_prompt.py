"""What the model is asked for when balancing, and what it may answer.

Separated from the generation loop because the two change for different reasons:
the wording and the answer schema are tuned against a model, the loop against a
budget. Everything here is pure — no I/O, no LLM call.
"""

from __future__ import annotations

import json
from dataclasses import dataclass

from pydantic import BaseModel, Field

from .fields import TextField, read_values, write_values
from .prompt_context import (
    FieldShape,
    guidance_states_a_length,
    label_name,
    typical_phrase,
)

# A cell is bounded by the gate, not here (``balance_gates``: an item holding one over
# MAX_VALUE_CHARS is discarded): one such value failed the schema of the whole answer,
# and the run it was paid in. The answer's size is bounded by its output tokens.
Value = str


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


@dataclass(frozen=True)
class PromptContext:
    """What the prompt knows about the dataset beyond the examples
    (``prompt_context`` reads it from the frame).

    ``label_name`` is the label as people read it; ``others`` the labels a new row must
    not read like, already one line each, and ``more_others`` how many were left out of
    that list; ``shapes`` one entry per field, in field order.
    """

    label_name: str | None = None
    others: tuple[str, ...] = ()
    more_others: int = 0
    shapes: tuple[FieldShape | None, ...] = ()


# Bounds one example cell in the prompt. A dataset cell can hold a whole article,
# and a handful of those would cost more per batch than the generation itself.
_EXAMPLE_CHARS = 400

# The avoid block: the most recent titles, each cut to a title's length.
_AVOID_COUNT = 15
_AVOID_CHARS = 120

_BALANCE_PROMPT = """Kontext: Ein Katalog für BILDUNGSINHALTE (Lernmaterialien für \
Unterricht und Selbstlernen). Jeder Eintrag beschreibt EIN konkretes Lernmaterial, \
das zu „{label}“ gehört.
{contrast}
Felder eines Eintrags, genau in dieser Reihenfolge:
{field_lines}

Echte Einträge aus dem Datensatz als Vorbild für Ton und Stil, lange Felder gekürzt (NICHT kopieren):
{examples}

Erzeuge {n} NEUE, DEUTLICH VERSCHIEDENE Einträge zu „{label}“.

Regeln:
- Decke unterschiedliche Teilgebiete von „{label}“ ab, nicht nur das Naheliegendste.
- Richte Länge und Umfang nach den Angaben zu den Feldern, Ton und Stil nach den Beispielen.
- Vermeide diese bereits vorhandenen Titel: {avoid}
- KEINE Personennamen, E-Mail-Adressen, Telefonnummern, URLs, Anbieter- oder \
Institutionsnamen.
Antworte NUR mit JSON: {{"items": [{{"values": [{value_slots}]}}]}}"""


def shown_cells(row: dict, fields: list[TextField]) -> list[str]:
    """The cells of ``row`` exactly as the prompt shows them: normalised, and cut to
    what a prompt can afford.

    Everything that judges or sizes an answer uses this, not the full row — the
    model can only imitate, or copy, what it was shown. A floor or a budget computed
    from a 1,500-character cell the model saw 400 characters of asks for the wrong
    thing.
    """
    return [write_values(read_values(row.get(f.column), f), f)[:_EXAMPLE_CHARS] for f in fields]


def _avoid_block(titles: list[str]) -> str:
    """The titles to avoid, as ONE line.

    Whitespace is collapsed because a line break inside a title would otherwise write
    a new line — possibly an instruction — into the prompt. Blanks and repeats carry
    nothing, and a missing cell is not a title.
    """
    cleaned: list[str] = []
    known: set[str] = set()
    for title in titles:
        if not isinstance(title, str):
            continue
        text = " ".join(title.split())[:_AVOID_CHARS]
        if text and text.casefold() not in known:
            known.add(text.casefold())
            cleaned.append(text)
    return "; ".join(cleaned[-_AVOID_COUNT:]) or "—"


# The most output tokens one call is given, and what an answer spends beside its entries.
_MAX_OUTPUT_TOKENS = 16000
_BASE_TOKENS = 500


def _entry_tokens(examples: list[dict], fields: list[TextField],
                  context: PromptContext | None) -> int:
    chars = max((sum(map(len, shown_cells(row, fields))) for row in examples), default=0)
    if context is not None:
        chars = max(chars, sum(s.chars[1] for s in context.shapes if s and s.chars))
    return max(300, chars // 2)


def output_budget(
    examples: list[dict], fields: list[TextField], n: int, context: PromptContext | None = None,
) -> int:
    """Output tokens for a batch of ``n`` entries shaped like ``examples``.

    The run worker's formula (``runs.py``), sized from the longest example instead of
    a configured corridor -- or from the typical upper length the prompt asks for, when
    that is longer: the examples are typical rows now, not the longest, and an answer
    that follows the stated lengths must fit. The client default of 2,000 tokens --
    reasoning included -- truncates a batch of ten entries with descriptions.
    """
    return min(_MAX_OUTPUT_TOKENS, _BASE_TOKENS + n * _entry_tokens(examples, fields, context))


def entries_per_call(
    examples: list[dict], fields: list[TextField], batch_size: int,
    context: PromptContext | None = None,
) -> int:
    """``batch_size``, or fewer: as many entries as ``output_budget`` can give room.
    A batch past the cap is cut off at it, and a cut answer fails the call -- and the
    run with every row it had paid for."""
    fits = (_MAX_OUTPUT_TOKENS - _BASE_TOKENS) // _entry_tokens(examples, fields, context)
    return max(1, min(batch_size, fits))


def _field_line(index: int, field: TextField, shape: FieldShape | None) -> str:
    """The field, what it is for, what KIND of value it holds and how long it usually
    is -- a description and a keyword list differ in all three, and a model that is not
    told writes both the same way."""
    if field.separator:
        details = [f'Liste in EINEM String, getrennt durch "{field.separator}"']
        if field.min_values > 1:
            details.append(f"mindestens {field.min_values} Werte")
    else:
        details = ["Freitext, EIN Wert"]
    typical = "" if guidance_states_a_length(field) else typical_phrase(field, shape)
    if typical:
        details.append(typical)
    guidance = f" — {field.guidance}" if field.guidance else ""
    return f"{index}. {field.column}{guidance} ({'; '.join(details)})"


def _contrast(label: str, context: PromptContext) -> str:
    """The other labels, so the new rows are unmistakably this one -- a row that would
    fit a neighbour as well teaches the classifier to confuse the two."""
    if not context.others:
        return ""
    listed = "; ".join(context.others)
    if context.more_others:
        listed += f" (und {context.more_others} weitere)"
    return (f"\nAndere Kategorien in diesem Datensatz: {listed}\n"
            f"Jeder neue Eintrag muss eindeutig zu „{label}“ passen und darf nicht ebenso gut "
            f"zu einer dieser anderen Kategorien passen.\n")


def build_balance_prompt(
    label: str,
    examples: list[dict],
    fields: list[TextField],
    *,
    n: int,
    avoid_titles: list[str],
    context: PromptContext | None = None,
) -> str:
    """The generation prompt for one batch of ``label``.

    ``examples`` are real rows as ``{column: cell}``; they are rendered in the SAME
    shape as the requested answer, because a prompt that shows one format and asks
    for another gets both blended (found in the api_v3 generation experiment).
    ``avoid_titles`` are values of the FIRST field already present or produced — the
    block that keeps a batch from collapsing into variants of one item. ``context``
    names the label as people read it, the labels to keep apart from, and the typical
    shape of each field (``prompt_context``); without it the prompt still states each
    field's kind.
    """
    rendered: list[str] = []
    for row in examples:
        cells = shown_cells(row, fields)
        if any(cells):  # a row with no text is an example of nothing
            rendered.append(json.dumps(cells, ensure_ascii=False))
    if not rendered:
        raise ValueError(f"Label {label!r} has no examples to generate from.")

    context = context or PromptContext()
    # From the data: a line break must not start an instruction, nor a quote end ours.
    name = label_name(label, context.label_name)
    shapes = list(context.shapes) or [None] * len(fields)
    return _BALANCE_PROMPT.format(
        label=name,
        contrast=_contrast(name, context),
        field_lines="\n".join(_field_line(i, f, shapes[i - 1])
                               for i, f in enumerate(fields, start=1)),
        examples="\n".join(rendered),
        n=n,
        avoid=_avoid_block(avoid_titles),
        value_slots=", ".join('"..."' for _ in fields),
    )
