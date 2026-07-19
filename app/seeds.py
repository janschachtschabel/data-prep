"""Seed pools per concept — the anchor examples every generation prompt builds on.

Two sources: distillation from a PII-scrubbed reference set (hybrid mode) and
LLM bootstrap from pure vocabulary context (vocab-only mode). A concept without
reference rows is anchored by its OWN label plus its broader path and scheme
title — raw URIs mean nothing to a language model (hard project lesson).

simplify: distillation dedupes exactly; semantic near-duplicate pruning plugs
in when embeddings.py lands (M7).
"""

from __future__ import annotations

import json
import random

import pandas as pd
from pydantic import BaseModel, Field

from .llm import LlmSession
from .pii import scrub
from .security import safe_name
from .settings import Settings
from .textnorm import split_labels
from .vocab import Vocabulary


class SeedItem(BaseModel):
    """One seed example — also the LLM structured-output schema."""

    title: str = Field(min_length=1, max_length=300)
    description: str = Field(default="", max_length=2000)
    keywords: str = Field(default="", max_length=500)


class SeedBatch(BaseModel):
    seeds: list[SeedItem]


MATERIAL_TYPES = (
    "Erklärvideo, Arbeitsblatt, Quiz, interaktive Übung, Webseite/Portal, Podcast, "
    "Unterrichtsentwurf, Lernspiel, Simulation, Präsentation"
)

# German on purpose: the catalog is German-language educational metadata and the
# recipe (names not URIs, catalog framing, mixed material types, PII ban) is the
# empirically corrected one from the api_v3 generation experiment.
_BOOTSTRAP_PROMPT = """Kontext: Ein Katalog für BILDUNGSINHALTE (Lernmaterialien für Unterricht \
und Selbstlernen). Fachgebiet/Thema: "{label}"{alt_clause}.
Einordnung in der Systematik: {anchor}.

Erzeuge {n} DEUTLICH VERSCHIEDENE Beispiel-Katalogeinträge zu "{label}" — sie dienen als \
Saatgut für die spätere Generierung.

Regeln:
- MISCHE die Materialtypen: {types} — der Typ soll aus Titel oder Beschreibung hervorgehen.
- Decke unterschiedliche Teilgebiete von "{label}" ab, nicht nur die typischsten Themen.
- Nutze fachtypische Begriffe natürlich im Text.
- description: 100-400 Zeichen, sachlich beschreibend. keywords: 3-6 Schlagwörter, kommagetrennt.
- KEINE Personennamen, E-Mail-Adressen, Telefonnummern, URLs, Anbieter- oder Institutionsnamen."""


def bootstrap_prompt(vocab: Vocabulary, uri: str, n: int) -> str:
    """Build the vocab-only generation prompt: label + alt labels + hierarchy anchor."""
    label = vocab.label(uri)  # raises ValueError for unknown concepts
    concept = vocab.concepts[uri]
    alts = [a for lang in ("de", "en") for a in concept.alt_labels.get(lang, []) if a != label]
    alt_clause = f" (auch: {', '.join(alts[:4])})" if alts else ""
    return _BOOTSTRAP_PROMPT.format(
        label=label, alt_clause=alt_clause, anchor=vocab.anchor(uri), n=n, types=MATERIAL_TYPES
    )


def distill_from_reference(
    df: pd.DataFrame,
    meta: dict,
    vocab: Vocabulary,
    *,
    per_concept: int = 6,
    rng_seed: int = 42,
) -> dict[str, list[dict]]:
    """Exact-deduped, capped seed pools from a reference frame (already scrubbed).

    Only concepts that exist in the vocabulary AND have rows appear in the
    result; the seeded sample keeps runs reproducible.
    """
    df = df.fillna("")
    title_col, desc_col, *rest = meta["text_columns"]
    keyword_col = rest[0] if rest else None

    pools: dict[str, list[dict]] = {}
    seen: dict[str, set[str]] = {}
    for _, row in df.iterrows():
        title = str(row.get(title_col, "")).strip()
        description = str(row.get(desc_col, "")).strip()
        if not title and not description:
            continue
        keywords = str(row.get(keyword_col, "")).strip() if keyword_col else ""
        fingerprint = f"{title}\n{description}".lower()
        for uri in split_labels(str(row.get(meta["label_column"], ""))):
            if uri not in vocab.concepts or fingerprint in seen.setdefault(uri, set()):
                continue
            seen[uri].add(fingerprint)
            pools.setdefault(uri, []).append(
                {"title": title, "description": description, "keywords": keywords, "source": "distilled"}
            )
    rng = random.Random(rng_seed)
    for uri, seeds in pools.items():
        if len(seeds) > per_concept:
            pools[uri] = rng.sample(seeds, per_concept)
    return pools


async def bootstrap_concept(session: LlmSession, vocab: Vocabulary, uri: str, n: int) -> list[dict]:
    """LLM-generate up to ``n`` seeds for one concept; outputs are PII-scrubbed
    (defense in depth on top of the prompt ban) and marked ``source=bootstrap``."""
    batch = await session.complete(bootstrap_prompt(vocab, uri, n), SeedBatch, max_output_tokens=3000)
    seeds: list[dict] = []
    for item in batch.seeds[:n]:
        seeds.append(
            {
                "title": scrub(item.title)[0],
                "description": scrub(item.description)[0],
                "keywords": scrub(item.keywords)[0],
                "source": "bootstrap",
            }
        )
    return seeds


# ------------------------------------------------------------------- store ----


def _seeds_dir(settings: Settings):
    directory = settings.data_dir / "seeds"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def _summary(payload: dict) -> dict:
    concepts = payload["concepts"]
    with_seeds = sum(1 for c in concepts.values() if c["seeds"])
    return {
        "name": payload["name"],
        "vocab": payload["vocab"],
        "reference": payload.get("reference"),
        "concept_count": len(concepts),
        "concepts_with_seeds": with_seeds,
        "seed_count": sum(len(c["seeds"]) for c in concepts.values()),
        "concepts_with_terms": sum(1 for c in concepts.values() if c.get("terms")),
        "term_count": sum(len(c.get("terms", [])) for c in concepts.values()),
    }


def save_seed_set(settings: Settings, name: str, payload: dict) -> None:
    path = _seeds_dir(settings) / f"{safe_name(name, 'seed set name')}.json"
    path.write_text(json.dumps(payload, ensure_ascii=False), encoding="utf-8")


def load_seed_set(settings: Settings, name: str) -> dict | None:
    path = _seeds_dir(settings) / f"{safe_name(name, 'seed set name')}.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def list_seed_sets(settings: Settings) -> list[dict]:
    return [
        _summary(json.loads(path.read_text(encoding="utf-8")))
        for path in sorted(_seeds_dir(settings).glob("*.json"))
    ]


def delete_seed_set(settings: Settings, name: str) -> bool:
    path = _seeds_dir(settings) / f"{safe_name(name, 'seed set name')}.json"
    if not path.exists():
        return False
    path.unlink()
    return True


def seed_set_summary(payload: dict) -> dict:
    """Public alias used by the routes for consistent list/detail summaries."""
    return _summary(payload)
