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
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import Annotated

import pandas as pd
from pydantic import BaseModel, Field, StringConstraints

from ..pii import scrub
from .fields import TextField, is_gap, merge_values, read_values, write_values

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


_PROMPT = """Kontext: Metadaten für ein Lernmaterial (Bildungsinhalt).
{context}

Aufgabe: Ergänze das Feld "{column}". {shape}
{existing}{guidance}KEINE Personennamen, E-Mails, Telefonnummern, URLs oder Anbieternamen."""


def _shape(target: TextField) -> str:
    """The task, stated even when nobody typed a guidance line — without it the model
    was shown context and a PII rule and asked for nothing in particular."""
    if target.separator is None:
        return "Es enthält genau EINEN Wert."
    return (f'Es enthält eine Liste (in der Zelle getrennt durch "{target.separator}"); '
            f"gib mindestens {target.min_values} Werte zurück, jeden als eigenen Eintrag.")


def _mark(existing: str, field: str) -> str:
    marks = [m for m in str(existing or "").split(",") if m.strip()]
    if field not in marks:
        marks.append(field)
    return ",".join(marks)


def _prompt_for(
    row: pd.Series, fields: list[TextField], target: TextField, existing: list[str]
) -> str:
    """The other fields as context; the task, what the list already holds, and the
    target's own guidance as the instruction."""
    context = []
    for field in fields:
        if field.column == target.column:
            continue
        values = read_values(row.get(field.column), field)
        if values:
            context.append(f'{field.column}: "{write_values(values, field)}"')
    held = (f"Bereits vorhanden, nicht wiederholen: {write_values(existing, target)}\n"
            if existing else "")
    return _PROMPT.format(
        context="\n".join(context), column=target.column, shape=_shape(target),
        existing=held, guidance=f"{target.guidance}\n" if target.guidance else "",
    )


async def enrich_dataset(
    df: pd.DataFrame,
    *,
    fields: list[TextField],
    target_field: str,
    complete: Complete,
    limit: int = 5000,
) -> tuple[pd.DataFrame, dict]:
    """Fill gaps in ``target_field`` via ``complete``; returns the new frame and
    ``{field, rows, enriched}``.

    Raises ``ValueError`` when the target is not one of ``fields`` — a silent no-op
    there would be indistinguishable from "nothing needed enrichment".
    """
    target = next((f for f in fields if f.column == target_field), None)
    if target is None:
        raise ValueError(f"Target field {target_field!r} is not among the fields.")

    new = df.copy()
    if "enriched_fields" not in new.columns:
        new["enriched_fields"] = ""
    else:
        new["enriched_fields"] = new["enriched_fields"].fillna("")

    enriched = 0
    for idx in new.index:
        if enriched >= limit:
            break
        cell = new.at[idx, target.column]
        if not is_gap(cell, target):
            continue
        existing = read_values(cell, target)
        result = await complete(_prompt_for(new.loc[idx], fields, target, existing), FieldValues)
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

    return new, {"field": target.column, "rows": int(len(df)), "enriched": enriched}
