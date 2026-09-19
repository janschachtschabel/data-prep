"""Refine enrichment: ADDITIVE LLM completion of missing fields.

Only fills gaps — a field holding fewer values than it asks for — and never
overwrites existing curated content: a short LIST is extended, not replaced, and an
answer that adds nothing leaves the cell alone. Every change is recorded in an
``enriched_fields`` column so the provenance is auditable. LLM outputs are
PII-scrubbed as defense in depth. The LLM call is injected as ``complete`` so the
engine is testable without a real model.

The fields are described by the caller (:class:`app.refine.fields.TextField`) rather
than fixed to title/description/keywords. What used to be two hard-coded prompts is
now one prompt built from the other fields' values plus the target field's own
``guidance``, so a column nobody anticipated — a semicolon-separated author list —
enriches without a new branch.

The prompt also says how the row is classified (``label_column``, by display name where
the export has one), so the added text fits the label it trains, and how long the field
typically is in the rows people wrote (``prompt_context``).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Annotated

import pandas as pd
from pydantic import BaseModel, Field, StringConstraints

from ..pii import scrub
from ..textnorm import split_labels
from .fields import TextField, is_gap, merge_values, read_values, write_values
from .prompt_context import display_names, field_shapes, guidance_names_a_number, typical_phrase
from .provenance import ENRICHED_FIELDS, GENERATED_FOR, is_marked

Complete = Callable[[str, type[BaseModel]], Awaitable[BaseModel]]

# Bounded per value and per list: a model that runs away must not write a megabyte
# into one cell. 2000 characters is what the description prompt has always allowed.
Value = Annotated[str, StringConstraints(max_length=2000)]


class FieldValues(BaseModel):
    """What the model returns for one field: always a list, even for one value.

    A single-valued field takes the first (``write_values``); asking for a list
    keeps the schema identical across fields, which is what lets one prompt serve
    all of them.
    """

    values: list[Value] = Field(default_factory=list, max_length=20)


_PROMPT = """Kontext: Metadaten für ein Lernmaterial (Bildungsinhalt){classified}.
{context}

Aufgabe: Ergänze das Feld "{column}". {shape}{typical}
{existing}{guidance}{fits}KEINE Personennamen, E-Mails, Telefonnummern, URLs oder Anbieternamen."""

# How many of a row's labels the prompt names, and at most how long each: a free-text
# label column can hold dozens, and every enriched row pays for them.
_MAX_LABELS = 5
_LABEL_CHARS = 60


def _shape(target: TextField) -> str:
    """The task, stated even when nobody typed a guidance line — without it the model
    was shown context and a PII rule and asked for nothing in particular."""
    if target.separator is None:
        return "Es enthält genau EINEN Wert."
    return (f'Es enthält eine Liste (in der Zelle getrennt durch "{target.separator}"); '
            f"gib mindestens {target.min_values} Werte zurück, jeden als eigenen Eintrag.")


def _one_line(text: str) -> str:
    """Dataset text as one line: a line break in a cell must not start a new line —
    possibly an instruction — in the prompt."""
    return " ".join(text.split())


def _mark(existing: str, field: str) -> str:
    marks = [m for m in str(existing or "").split(",") if m.strip()]
    if field not in marks:
        marks.append(field)
    return ",".join(marks)


def _typical_sentence(df: pd.DataFrame, target: TextField) -> str:
    """How long ``target`` typically is in the rows people wrote, as one sentence -- or
    nothing when its guidance names a number of its own or the rows say too little.

    A generated row, or a cell an earlier enrichment filled, would teach the model its
    own lengths back, so both are left out of the measurement.
    """
    if guidance_names_a_number(target):
        return ""
    generated = df[GENERATED_FOR] if GENERATED_FOR in df.columns else [None] * len(df)
    enriched = df[ENRICHED_FIELDS] if ENRICHED_FIELDS in df.columns else [None] * len(df)
    cells = []
    for cell, made, filled in zip(df[target.column], generated, enriched, strict=True):
        if is_marked(made) or (is_marked(filled) and target.column in
                               [part.strip() for part in str(filled).split(",")]):
            continue
        cells.append([write_values(read_values(cell, target), target)])
    phrase = typical_phrase(target, field_shapes(cells, [target])[0])
    return f" {phrase[0].upper()}{phrase[1:]}." if phrase else ""


def _classification(row: pd.Series, label_column: str | None, separator: str,
                    names: dict[str, str]) -> str:
    """The row's labels as people read them, one line, for the prompt's first sentence."""
    if label_column is None:
        return ""
    labels = split_labels(row.get(label_column), separator)[:_MAX_LABELS]
    return ", ".join(_one_line(names.get(label, label))[:_LABEL_CHARS] for label in labels)


def _prompt_for(
    row: pd.Series, fields: list[TextField], target: TextField, existing: list[str],
    *, classified: str = "", typical: str = "",
) -> str:
    """The other fields as context; the task, what the list already holds, and the
    target's own guidance as the instruction. ``classified`` names the row's labels,
    ``typical`` is the sentence on the field's usual length."""
    context = []
    for field in fields:
        if field.column == target.column:
            continue
        values = read_values(row.get(field.column), field)
        if values:
            context.append(f'{field.column}: "{_one_line(write_values(values, field))}"')
    held = (f"Bereits vorhanden, nicht wiederholen: {_one_line(write_values(existing, target))}\n"
            if existing else "")
    return _PROMPT.format(
        classified=f", eingeordnet unter „{classified}“" if classified else "",
        context="\n".join(context), column=target.column, shape=_shape(target),
        typical=typical, existing=held,
        guidance=f"{target.guidance}\n" if target.guidance else "",
        fits="Die Ergänzung soll zu dieser Einordnung passen.\n" if classified else "",
    )


async def enrich_dataset(
    df: pd.DataFrame,
    *,
    fields: list[TextField],
    target_field: str,
    complete: Complete,
    limit: int = 5000,
    stop_on: tuple[type[BaseException], ...] = (),
    label_column: str | None = None,
    label_separator: str = ",",
) -> tuple[pd.DataFrame, dict]:
    """Fill gaps in ``target_field`` via ``complete``; returns the new frame and
    ``{field, rows, enriched, stopped}``.

    ``label_column`` (when the frame has it) names each row's labels in its prompt, by
    ``<label_column>_DISPLAYNAME`` where the export carries one.

    An exception in ``stop_on`` — a budget cap, typically — ends the run early
    instead of discarding it: the rows enriched until then are returned, and
    ``stopped`` says why the run ended. Anything else propagates.

    Raises ``ValueError`` when the target is not one of ``fields`` — a silent no-op
    there would be indistinguishable from "nothing needed enrichment".
    """
    target = next((f for f in fields if f.column == target_field), None)
    if target is None:
        raise ValueError(f"Target field {target_field!r} is not among the fields.")

    if label_column is not None and label_column not in df.columns:
        label_column = None
    # Both walk the whole frame -- off the event loop, like balancing's first read.
    names, typical = await asyncio.to_thread(
        lambda: ({} if label_column is None else display_names(df, label_column, label_separator),
                 _typical_sentence(df, target)))

    new = df.copy()
    if "enriched_fields" not in new.columns:
        new["enriched_fields"] = ""
    else:
        new["enriched_fields"] = new["enriched_fields"].fillna("")

    enriched = 0
    calls = 0  # what `limit` caps: model calls, whether or not they changed a row
    stopped: str | None = None
    for idx in new.index:
        if calls >= limit:
            break
        cell = new.at[idx, target.column]
        if not is_gap(cell, target):
            continue
        existing = read_values(cell, target)
        try:
            row = new.loc[idx]
            prompt = _prompt_for(row, fields, target, existing, typical=typical,
                                 classified=_classification(row, label_column, label_separator, names))
            result = await complete(prompt, FieldValues)
        except stop_on as signal:
            stopped = str(signal) or signal.__class__.__name__
            break
        calls += 1
        # Scrubbed per value, as the model returned it; a list value that still holds
        # the separator ("Optik, Licht") is split so the merge can see both parts.
        answered = [part for value in result.values  # type: ignore[attr-defined]
                    for part in read_values(scrub(value)[0], target)]
        merged = merge_values(existing, answered) if target.separator else answered[:1]
        if not merged or merged == existing:
            continue  # nothing new: the cell stays as it was, and is not counted
        new.at[idx, target.column] = write_values(merged, target)
        new.at[idx, "enriched_fields"] = _mark(new.at[idx, "enriched_fields"], target.column)
        enriched += 1

    return new, {"field": target.column, "rows": int(len(df)), "enriched": enriched,
                 "stopped": stopped}
