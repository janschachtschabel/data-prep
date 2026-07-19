"""Stage 5 — prompt building and acceptance gates for generation batches.

The prompt follows the empirically corrected recipe (labels not URIs, catalog
framing, material-type mixing, facet rotation, few-shot seeds) plus an explicit
"avoid these titles" block against prototype collapse. Gates at accept time:
description-length corridor, exact dedupe (fingerprint), PII scrub (mask) as
defense in depth on top of the prompt ban.
"""

from __future__ import annotations

import json
import uuid
from datetime import UTC, datetime

from pydantic import BaseModel

from .pii import scrub
from .seeds import MATERIAL_TYPES, SeedItem
from .vocab import Vocabulary

# Rotating audience/angle per batch — breadth against prototype collapse
# (proven set from the api_v3 generation experiment).
FACETS = [
    "Grundschule / einfache Sprache",
    "Sekundarstufe I",
    "Sekundarstufe II / Oberstufe",
    "berufliche Bildung / Ausbildung",
    "Erwachsenenbildung / Weiterbildung",
    "Randthemen und weniger typische Teilgebiete des Fachs",
    "fächerübergreifende Bezüge (aber das Fach bleibt der Kern)",
    "Alltagsbezug und aktuelle Anlässe",
    "Grundbegriffe und Einstieg",
    "Vertiefung für Fortgeschrittene",
]


class GenBatch(BaseModel):
    """LLM structured output for one generation batch."""

    items: list[SeedItem]


_GENERATION_PROMPT = """Kontext: Ein Katalog für BILDUNGSINHALTE (Lernmaterialien für Unterricht \
und Selbstlernen). Jeder Eintrag beschreibt EIN konkretes Lernmaterial zum Fachgebiet "{label}".
Einordnung in der Systematik: {anchor}.{guidance}

Echte Katalogeinträge als Stil- und Längenvorbild (NICHT kopieren):
{examples}

Erzeuge {n} NEUE, DEUTLICH VERSCHIEDENE Katalogeinträge zu "{label}".
Zielgruppe/Blickwinkel dieser Runde: {facet}.

Regeln:
- MISCHE die Materialtypen: {types} — der Typ soll aus Titel oder Beschreibung hervorgehen.
- Decke unterschiedliche Teilgebiete von "{label}" ab, nicht nur die typischsten Themen.
- Nutze fachtypische Begriffe natürlich im Text.{terms}
- title: kurz und prägnant (max. 80 Zeichen), OHNE Beschreibungstext oder Trennzeichen.
- description: {low}-{high} Zeichen, sachlich beschreibend. keywords: 3-6 Schlagwörter, kommagetrennt.
- Vermeide Titel, die es schon gibt: {avoid}
- KEINE Personennamen, E-Mail-Adressen, Telefonnummern, URLs, Anbieter- oder Institutionsnamen.
Antworte NUR mit JSON: {{"items": [{{"title": "...", "description": "...", "keywords": "..."}}]}}"""


def build_generation_prompt(
    vocab: Vocabulary,
    uri: str,
    *,
    seeds: list[dict],
    n: int,
    corridor: tuple[int, int],
    facet: str,
    avoid_titles: list[str],
    guidance: str = "",
    terms: list[str] | None = None,
) -> str:
    # Examples in the SAME JSON shape as the requested output — a pipe-line
    # format here made gpt-5.4-nano blend both formats and stuff "| Beschreibung:"
    # into its title field (found in the live E2E run).
    examples = "\n".join(
        json.dumps(
            {"title": s["title"][:80], "description": s["description"][:220],
             "keywords": s["keywords"][:80]},
            ensure_ascii=False,
        )
        for s in seeds
    ) or "(noch keine Beispiele — orientiere dich an der Einordnung oben)"
    avoid = "; ".join(avoid_titles[-15:]) or "—"
    low, high = corridor
    # Optional user context — steers vocab-only generation when the label alone
    # is not self-explanatory (e.g. which "discipline" scheme, which level).
    guidance_clause = (
        f"\nVorgabe des Nutzers (bitte strikt beachten): {guidance.strip()}"
        if guidance.strip()
        else ""
    )
    # Per-concept term bank (mined from the reference, optionally LLM-refined):
    # steers coverage and, by rotating a subset per batch, varies the output.
    terms_clause = (
        "\n- Beziehe passende Fachbegriffe/Entitäten ein und variiere sie über die Einträge "
        f"(nicht alle in jeden): {', '.join(terms)}."
        if terms
        else ""
    )
    return _GENERATION_PROMPT.format(
        label=vocab.label(uri), anchor=vocab.anchor(uri), examples=examples, n=n,
        facet=facet, types=MATERIAL_TYPES, low=low, high=high, avoid=avoid,
        guidance=guidance_clause, terms=terms_clause,
    )


def accept_item(
    item: SeedItem, corridor: tuple[int, int], seen: set[str], counters: dict
) -> dict | None:
    """Gate one generated item; returns the sample dict or ``None`` (counted)."""
    low, high = corridor
    if not low <= len(item.description) <= high:
        counters["discarded_length"] += 1
        return None
    fingerprint = f"{item.title}\n{item.description}".lower()
    if fingerprint in seen:
        counters["discarded_duplicate"] += 1
        return None
    seen.add(fingerprint)
    # 'generated' is counted by the run worker AFTER the semantic gates (M7) —
    # this function only owns the cheap gates.
    return {
        "id": uuid.uuid4().hex,
        "title": scrub(item.title)[0],
        "description": scrub(item.description)[0],
        "keywords": scrub(item.keywords)[0],
        "synthetic": True,
        "status": "passed",
        "created_at": datetime.now(UTC).isoformat(),
    }
