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

import pandas as pd
from pydantic import BaseModel, Field

from ..pii import scrub
from ..textnorm import split_labels
from .analyze import _combined_texts
from .fields import TextField, is_gap, merge_values, read_values, write_values
from .prompt_context import (
    MAX_VALUE_CHARS,
    display_names,
    field_shapes,
    guidance_states_a_length,
    label_name,
    one_line,
    typical_phrase,
)
from .provenance import ENRICHED_FIELDS, GENERATED_FOR, enriched_columns, is_marked

Complete = Callable[[str, type[BaseModel]], Awaitable[BaseModel]]

# Bounded where the answer is read, not by its schema: a value over MAX_VALUE_CHARS is
# dropped alone and a list is cut after _MAX_ANSWER_VALUES -- in the schema either failed
# the call, and the run it was paid in. The answer's size is bounded by its output tokens.
Value = str
_MAX_ANSWER_VALUES = 50


class FieldValues(BaseModel):
    """What the model returns for one field: always a list, even for one value.

    A single-valued field takes the first (``write_values``); asking for a list
    keeps the schema identical across fields, which is what lets one prompt serve
    all of them.
    """

    values: list[Value] = Field(default_factory=list)


_PROMPT = """Kontext: Metadaten für ein Lernmaterial (Bildungsinhalt){classified}.
{context}

Aufgabe: Ergänze das Feld "{column}". {shape}{typical}
{existing}{guidance}{fits}KEINE Personennamen, E-Mails, Telefonnummern, URLs oder Anbieternamen."""

# How many of a row's labels the prompt names: a free-text label column can hold
# dozens, and every enriched row pays for them.
_MAX_LABELS = 5


def _shape(target: TextField) -> str:
    """The task, stated even when nobody typed a guidance line — without it the model
    was shown context and a PII rule and asked for nothing in particular."""
    if target.separator is None:
        return "Es enthält genau EINEN Wert."
    return (f'Es enthält eine Liste (in der Zelle getrennt durch "{target.separator}"); '
            f"gib mindestens {target.min_values} Werte zurück, jeden als eigenen Eintrag.")


def _mark(existing: str, field: str) -> str:
    marks = [m.strip() for m in str(existing or "").split(",") if m.strip()]
    if field not in marks:
        marks.append(field)
    return ",".join(marks)


def _typical_sentence(df: pd.DataFrame, target: TextField) -> str:
    """How long ``target`` typically is in the rows people wrote, as one sentence -- or
    nothing when its guidance names a number of its own or the rows say too little.

    A generated row, or a cell an earlier enrichment filled, would teach the model its
    own lengths back, so both are left out of the measurement. For a list the count is
    the whole cell's: the model's values are added to the ones the row already holds.
    """
    if guidance_states_a_length(target):
        return ""
    generated = df[GENERATED_FOR] if GENERATED_FOR in df.columns else [None] * len(df)
    enriched = df[ENRICHED_FIELDS] if ENRICHED_FIELDS in df.columns else [None] * len(df)
    cells = []
    for cell, made, filled in zip(df[target.column], generated, enriched, strict=True):
        if is_marked(made) or target.column in enriched_columns(filled):
            continue
        cells.append([write_values(read_values(cell, target), target)])
    phrase = typical_phrase(target, field_shapes(cells, [target])[0])
    if not phrase:
        return ""
    total = " insgesamt" if target.separator else ""
    return f" {phrase[0].upper()}{phrase[1:]}{total}."


def _read_context(
    df: pd.DataFrame, target: TextField, label_column: str | None, separator: str,
) -> tuple[dict[str, str], str]:
    """The display names and the typical-length sentence -- both walk the whole frame,
    so the caller runs this off the event loop, as balancing does its first read."""
    names = {} if label_column is None else display_names(df, label_column, separator)
    return names, _typical_sentence(df, target)


Twin = tuple[str, tuple[str, ...]]


def _twins_and_gaps(df: pd.DataFrame, fields: list[TextField], target: TextField,
                    label_column: str | None, separator: str) -> tuple[list[Twin], list[bool]]:
    """Per row: what makes two rows twins, and whether its target cell is a gap.

    Twins carry the same text across ``fields`` -- cleaned and joined, as the split and
    api_v3 read text (``_combined_texts``) -- and the same labels, which their prompt
    names: rows alike only in empty fields but filed apart are no twins. The key is this
    enrichment's, so it matches a split reading these columns or more; a split reading
    FEWER can hold as one text two rows answered apart (README names the precondition).
    Read column by column, off the event loop: per-row access on a large frame takes
    seconds."""
    texts = _combined_texts(df, [field.column for field in fields])
    labels = ([tuple(sorted(set(split_labels(cell, separator)))) for cell in df[label_column]]
              if label_column is not None else [()] * len(df))
    return (list(zip(texts, labels, strict=True)),
            [is_gap(cell, target) for cell in df[target.column]])


def _classification(row: pd.Series, label_column: str | None, separator: str,
                    names: dict[str, str]) -> str:
    """The row's labels as people read them, one line, for the prompt's first sentence."""
    if label_column is None:
        return ""
    labels = split_labels(row.get(label_column), separator)[:_MAX_LABELS]
    return ", ".join(label_name(label, names.get(label)) for label in labels)


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
            context.append(f'{field.column}: "{one_line(write_values(values, field))}"')
    held = (f"Bereits vorhanden, nicht wiederholen: {one_line(write_values(existing, target))}\n"
            if existing else "")
    return _PROMPT.format(
        classified=f", eingeordnet unter „{classified}“" if classified else "",
        context="\n".join(context), column=target.column, shape=_shape(target),
        typical=typical, existing=held,
        guidance=f"{target.guidance}\n" if target.guidance else "",
        # Fitting it, not naming it: the label written into a row that trains it is a
        # feature the real rows do not have.
        fits=("Die Ergänzung soll zu dieser Einordnung passen, sie aber nicht selbst nennen.\n"
              if classified else ""),
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
    names, typical = await asyncio.to_thread(
        _read_context, df, target, label_column, label_separator)

    new = df.copy()
    if ENRICHED_FIELDS not in new.columns:
        new[ENRICHED_FIELDS] = ""
    else:
        new[ENRICHED_FIELDS] = new[ENRICHED_FIELDS].fillna("")

    enriched = 0
    calls = 0  # what `limit` caps: model calls, whether or not they changed a row
    stopped: str | None = None
    # Twins -- the same text across `fields`, the same labels -- share one answer.
    # Enriched apart, the untouched twin keeps the text its enriched twin trains on and
    # may validate on it: the holdout split and api_v3's dedupe know a row by its text.
    # So a twin takes the answer its first row got, past `limit` or a stop too, and
    # without a call of its own; each row merges it with the values it holds itself.
    twins, gaps = await asyncio.to_thread(_twins_and_gaps, df, fields, target,
                                          label_column, label_separator)
    answers: dict[Twin, list[str]] = {}
    for idx, twin, gap in zip(new.index, twins, gaps, strict=True):
        if not gap:
            continue
        existing = read_values(new.at[idx, target.column], target)
        if twin not in answers:
            if calls >= limit or stopped is not None:
                continue
            try:
                row = new.loc[idx]
                prompt = _prompt_for(row, fields, target, existing, typical=typical,
                                     classified=_classification(row, label_column,
                                                                label_separator, names))
                result = await complete(prompt, FieldValues)
            except stop_on as signal:
                stopped = str(signal) or signal.__class__.__name__
                continue
            calls += 1
            # Scrubbed per value, as the model returned it; a list value that still holds
            # the separator ("Optik, Licht") is split so the merge can see both parts. A
            # single-valued field takes the first value -- a spare is no answer -- and a
            # value too long is dropped.
            parts = [part for value in result.values  # type: ignore[attr-defined]
                     for part in read_values(scrub(value)[0], target)]
            answers[twin] = [part for part in (parts if target.separator else parts[:1])
                             if len(part) <= MAX_VALUE_CHARS][:_MAX_ANSWER_VALUES]
        merged = merge_values(existing, answers[twin]) if target.separator else answers[twin]
        if not merged or merged == existing:
            continue  # nothing new: the cell stays as it was, and is not counted
        new.at[idx, target.column] = write_values(merged, target)
        new.at[idx, ENRICHED_FIELDS] = _mark(new.at[idx, ENRICHED_FIELDS], target.column)
        enriched += 1

    return new, {"field": target.column, "rows": int(len(df)), "enriched": enriched,
                 "stopped": stopped}
