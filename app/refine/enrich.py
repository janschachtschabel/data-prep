"""Refine enrichment: ADDITIVE LLM completion of missing fields.

Only fills gaps (empty description, too-few keywords) — it never overwrites
existing curated content — and records every change in an ``enriched_fields``
column so the provenance is auditable. LLM outputs are PII-scrubbed as defense
in depth. The LLM call is injected as ``complete`` so the engine is testable
without a real model.
"""

from __future__ import annotations

from collections.abc import Awaitable, Callable
from typing import TypeVar

import pandas as pd
from pydantic import BaseModel, Field

from ..pii import scrub

T = TypeVar("T", bound=BaseModel)
Complete = Callable[[str, type[BaseModel]], Awaitable[BaseModel]]


class Keywords(BaseModel):
    keywords: str = Field(default="", max_length=500)


class Description(BaseModel):
    description: str = Field(default="", max_length=2000)


_KEYWORDS_PROMPT = """Kontext: Metadaten für ein Lernmaterial (Bildungsinhalt).
Titel: "{title}"
Beschreibung: "{description}"

Nenne 3-6 treffende deutsche Schlagwörter (kommagetrennt), die den Inhalt erschließen.
KEINE Personennamen, E-Mails, Telefonnummern, URLs oder Anbieternamen."""

_DESCRIPTION_PROMPT = """Kontext: Metadaten für ein Lernmaterial (Bildungsinhalt).
Titel: "{title}"
Schlagwörter: "{keywords}"

Schreibe eine sachliche Beschreibung (100-400 Zeichen), was dieses Material bietet.
KEINE Personennamen, E-Mails, Telefonnummern, URLs oder Anbieternamen."""


def _mark(cell: str, field: str) -> str:
    fields = [f for f in str(cell or "").split(",") if f.strip()]
    if field not in fields:
        fields.append(field)
    return ", ".join(fields)


async def enrich_dataset(
    df: pd.DataFrame,
    *,
    title_col: str,
    description_col: str,
    keyword_col: str,
    mode: str,
    min_keywords: int,
    complete: Complete,
    limit: int = 5000,
) -> tuple[pd.DataFrame, dict]:
    """Fill missing keywords or descriptions via ``complete``; returns the new
    frame and ``{mode, rows, enriched}``. Raises ``ValueError`` on unknown mode."""
    if mode not in ("keywords", "description"):
        raise ValueError(f"Unknown enrich mode {mode!r} (use 'keywords' or 'description').")
    new = df.copy()
    if "enriched_fields" not in new.columns:
        new["enriched_fields"] = ""
    else:
        new["enriched_fields"] = new["enriched_fields"].fillna("")

    enriched = 0
    for idx in new.index:
        if enriched >= limit:
            break
        title = str(new.at[idx, title_col] or "")
        description = str(new.at[idx, description_col] or "")
        keywords = str(new.at[idx, keyword_col] or "")
        if mode == "keywords":
            if len([k for k in keywords.split(",") if k.strip()]) >= min_keywords:
                continue
            result = await complete(_KEYWORDS_PROMPT.format(title=title, description=description), Keywords)
            new.at[idx, keyword_col] = scrub(result.keywords)[0]  # type: ignore[attr-defined]
        else:
            if description.strip():
                continue
            result = await complete(_DESCRIPTION_PROMPT.format(title=title, keywords=keywords), Description)
            new.at[idx, description_col] = scrub(result.description)[0]  # type: ignore[attr-defined]
        new.at[idx, "enriched_fields"] = _mark(new.at[idx, "enriched_fields"], mode)
        enriched += 1

    return new, {"mode": mode, "rows": int(len(df)), "enriched": enriched}
