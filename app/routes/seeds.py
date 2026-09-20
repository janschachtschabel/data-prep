"""Seed-set routes: build (distill or empty pools), list, detail, per-concept
LLM bootstrap, editor updates, delete."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException
from fastapi import Path as PathParam
from pydantic import BaseModel, Field

from ..llm import BudgetExceeded, LlmConfigError, LlmError, LlmOverride, session_for
from ..reference import load_reference
from ..security import MAX_NAME_BYTES, llm_override, refuse_existing, require_key, safe_name
from ..seeds import (
    SeedItem,
    bootstrap_concept,
    delete_seed_set,
    distill_from_reference,
    list_seed_sets,
    load_seed_set,
    save_seed_set,
    seed_set_exists,
    seed_set_summary,
)
from ..settings import Settings, get_settings
from ..terms import extract_candidates, refine_concept_terms
from ..vocab import Vocabulary, parse_vocabulary

router = APIRouter(prefix="/seeds", tags=["Seeds"], dependencies=[Depends(require_key)])

# The seed-set name every seed route takes in its path.
SeedSetName = Annotated[str, PathParam(description=(
    "A seed set as `GET /seeds` lists it. A plain name: path characters, or more than 200 bytes, "
    "are refused with 400; a name no seed set has answers 404."))]


class BuildRequest(BaseModel):
    name: str = Field(max_length=MAX_NAME_BYTES, description="Name to store the seed set under.")
    vocab: str = Field(max_length=MAX_NAME_BYTES, description=(
        "Name of a loaded vocabulary; the set gets a seed pool and a term bank per concept."))
    reference: str | None = Field(default=None, max_length=MAX_NAME_BYTES, description=(
        "Optional reference set to distill seeds and term banks from (hybrid mode); omitted = empty "
        "pools, to be filled by bootstrap or the editor (vocab-only mode)."))
    # Few-shot example seeds per concept (5-10 is plenty); this is NOT the term
    # bank scope, which is controlled separately below.
    per_concept: int = Field(default=6, ge=1, le=50, description=(
        "Maximum seeds distilled per concept (exact duplicates dropped, reproducible random sample)."))
    # Term bank: which reference columns feed it (None → the reference's configured
    # text columns), how many rows per label to mine, and the max distinct terms.
    keyword_columns: list[str] | None = Field(default=None, description=(
        "Reference columns split at `;` `,` `|` `/` into whole terms for the term bank; default: the "
        "reference's third text column."))
    term_columns: list[str] | None = Field(default=None, description=(
        "Reference columns mined for frequent content words (stop words dropped); default: the "
        "reference's first two text columns."))
    terms_max_rows: int = Field(default=500, ge=0, le=20000, description=(
        "Reference rows mined per concept for its term bank; 0 = no term bank."))
    terms_top_n: int = Field(default=60, ge=10, le=500, description=(
        "Maximum terms kept per concept, most frequent first; terms common to many concepts are dropped."))
    # Rebuilding discards every hand-edited and LLM-bootstrapped seed of the set.
    overwrite: bool = Field(default=False, description=(
        "Replace an existing seed set of that name, discarding its edited and bootstrapped seeds; "
        "without it the name is refused with 409."))


class BootstrapRequest(BaseModel):
    concept_uri: str = Field(max_length=500, description="URI of the concept to generate seeds for.")
    n: int = Field(default=4, ge=1, le=20, description=(
        "Number of seeds to ask the LLM for; those returned are appended to the concept's pool."))


class RefineTermsRequest(BaseModel):
    concept_uri: str = Field(max_length=500, description="URI of the concept whose term bank is refined.")
    n: int = Field(default=30, ge=1, le=100, description=(
        "Number of terms to ask for; the result (deduplicated, at most `n`) replaces the term bank."))
    context: str = Field(default="", max_length=2000, description=(
        "Optional steering added to the prompt, e.g. the level or focus the terms should fit."))


class ConceptSeedsPut(BaseModel):
    concept_uri: str = Field(max_length=500, description="URI of the concept whose seeds are replaced.")
    seeds: list[SeedItem] = Field(description=(
        "The concept's complete new seed list (may be empty), stored with `source=manual`. Each seed: "
        "`title` (1-300 characters), optional `description` (up to 2,000) and `keywords` (comma-separated, "
        "up to 500)."))


def _load_vocab(settings: Settings, name: str) -> Vocabulary:
    path = settings.data_dir / "vocabs" / f"{safe_name(name, 'vocabulary name')}.json"
    if not path.exists():
        raise HTTPException(status_code=400, detail=f"Vocabulary {name!r} not found — load it first.")
    return parse_vocabulary(json.loads(path.read_text(encoding="utf-8")))


def _load_or_404(settings: Settings, name: str) -> dict:
    payload = load_seed_set(settings, name)
    if payload is None:
        raise HTTPException(status_code=404, detail=f"Seed set {name!r} not found.")
    return payload


def _current_concept(settings: Settings, name: str, uri: str) -> tuple[dict, dict]:
    """The set and one concept as they are NOW, for applying an LLM result.

    Loaded again after the LLM call rather than reusing the copy from before it:
    saving that copy reverted every edit made in the meantime, and brought back
    a set that had been deleted. A set deleted meanwhile is a 404; one rebuilt
    without this concept is a conflict."""
    payload = _load_or_404(settings, name)
    concept = payload["concepts"].get(uri)
    if concept is None:
        raise HTTPException(
            status_code=409, detail="The seed set was rebuilt without this concept meanwhile."
        )
    return payload, concept


@router.get("", summary="List seed sets")
async def list_sets(settings: Settings = Depends(get_settings)) -> dict:
    """Summaries of all seed sets: vocabulary, reference, concept count, concepts with seeds, seed count,
    concepts with a term bank and term count."""
    return {"seed_sets": list_seed_sets(settings)}


@router.post("/build", summary="Build a seed set (distilled from a reference, or empty pools)")
async def build(req: BuildRequest, settings: Settings = Depends(get_settings)) -> dict:
    """Create a seed set for every concept of `vocab`. With `reference`, each concept's pool is distilled
    from the reference rows labelled with it, and a term bank is mined from the same rows; without one,
    the pools start empty. No LLM call. Returns the set's summary.

    Errors: 400 when the vocabulary or reference is not found or a name is invalid; 409 when the name is
    taken and `overwrite` is not set."""
    refuse_existing(seed_set_exists(settings, req.name), "Seed set", req.name, req.overwrite)
    vocab = _load_vocab(settings, req.vocab)
    pools: dict[str, list[dict]] = {}
    term_banks: dict[str, list[str]] = {}
    if req.reference:
        loaded = load_reference(settings, safe_name(req.reference, "reference name"))
        if loaded is None:
            raise HTTPException(status_code=400, detail=f"Reference set {req.reference!r} not found.")
        df, meta = loaded
        pools = distill_from_reference(df, meta, vocab, per_concept=req.per_concept)
        # Mine a candidate term bank per concept (LLM-free), separately from the
        # few-shot count; the user can refine it later via POST /{name}/terms.
        term_banks = extract_candidates(
            df, meta, vocab,
            keyword_columns=req.keyword_columns, term_columns=req.term_columns,
            top_n=req.terms_top_n, max_rows_per_label=req.terms_max_rows,
        )
    payload = {
        "name": req.name,
        "vocab": req.vocab,
        "reference": req.reference,
        "per_concept_target": req.per_concept,
        "created_at": datetime.now(UTC).isoformat(),
        "concepts": {
            uri: {"seeds": pools.get(uri, []), "terms": term_banks.get(uri, [])}
            for uri in vocab.concepts
        },
    }
    save_seed_set(settings, req.name, payload)
    return seed_set_summary(payload)


@router.get("/{name}", summary="Seed set details (all concepts and seeds)")
async def detail(name: SeedSetName, settings: Settings = Depends(get_settings)) -> dict:
    """The whole seed set: its parameters and, per concept URI, the seeds (each with its `source`:
    `distilled`, `bootstrap` or `manual`) and the term bank.

    Errors: 404 when no seed set has that name."""
    return _load_or_404(settings, name)


@router.post("/{name}/bootstrap", summary="LLM-generate seeds for ONE concept (vocab-only anchor)")
async def bootstrap(
    name: SeedSetName, req: BootstrapRequest,
    settings: Settings = Depends(get_settings),
    override: LlmOverride = Depends(llm_override),
) -> dict:
    """Ask the LLM (`seeds` endpoint) for `n` example entries of one concept, anchored in the vocabulary
    alone: its label, alternative labels and place in the hierarchy. The answers are PII-scrubbed and
    appended to the pool with `source=bootstrap`. Paid LLM call; honours `X-LLM-Key`/`X-LLM-Model`.
    Returns the number added and the LLM usage.

    Errors: 400 for a concept not in the set or a missing vocabulary; 404 for an unknown seed set; 409
    when the set was rebuilt without the concept during the call; 429 at a budget cap; 502 when the LLM
    call fails; 503 when no LLM key or endpoint is configured."""
    payload = _load_or_404(settings, name)
    if req.concept_uri not in payload["concepts"]:
        raise HTTPException(status_code=400, detail="Unknown concept in this seed set.")
    vocab = _load_vocab(settings, payload["vocab"])
    try:
        session = session_for("seeds", settings, override)
        seeds = await bootstrap_concept(session, vocab, req.concept_uri, req.n)
    except BudgetExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except LlmConfigError as exc:
        # Config problem (e.g. missing key) — we never reached the upstream, so
        # this is service-unavailable, not a bad-gateway response.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LlmError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    payload, concept = _current_concept(settings, name, req.concept_uri)
    concept["seeds"].extend(seeds)
    save_seed_set(settings, name, payload)
    return {"added": len(seeds), "usage": session.usage.as_dict()}


@router.post("/{name}/terms", summary="LLM-clean and extend a concept's term bank")
async def refine_terms(
    name: SeedSetName, req: RefineTermsRequest,
    settings: Settings = Depends(get_settings),
    override: LlmOverride = Depends(llm_override),
) -> dict:
    """Let the LLM (`seeds` endpoint) clean the concept's term bank and add missing domain terms,
    optionally steered by `context`. The result -- PII-scrubbed, deduplicated ignoring case, at most `n`
    terms -- replaces the term bank; generation runs rotate a subset of it into every batch prompt. Paid
    LLM call; honours `X-LLM-Key`/`X-LLM-Model`.

    Errors: 400 for a concept not in the set or a missing vocabulary; 404 for an unknown seed set; 409
    when the set was rebuilt without the concept during the call; 429 at a budget cap; 502 when the LLM
    call fails; 503 when no LLM key or endpoint is configured."""
    payload = _load_or_404(settings, name)
    concept = payload["concepts"].get(req.concept_uri)
    if concept is None:
        raise HTTPException(status_code=400, detail="Unknown concept in this seed set.")
    vocab = _load_vocab(settings, payload["vocab"])
    try:
        session = session_for("seeds", settings, override)
        terms = await refine_concept_terms(
            session, vocab, req.concept_uri, concept.get("terms", []), context=req.context, n=req.n
        )
    except BudgetExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except LlmConfigError as exc:
        # Config problem (e.g. missing key) — we never reached the upstream, so
        # this is service-unavailable, not a bad-gateway response.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LlmError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    payload, concept = _current_concept(settings, name, req.concept_uri)
    concept["terms"] = terms
    save_seed_set(settings, name, payload)
    return {"terms": terms, "usage": session.usage.as_dict()}


@router.put("/{name}/concepts", summary="Replace the seeds of one concept (editor)")
async def update_concept(name: SeedSetName, req: ConceptSeedsPut, settings: Settings = Depends(get_settings)) -> dict:
    """Replace one concept's seeds with the list sent, each marked `source=manual`; the term bank is kept.

    Errors: 400 for a concept not in the set; 404 for an unknown seed set."""
    payload = _load_or_404(settings, name)
    if req.concept_uri not in payload["concepts"]:
        raise HTTPException(status_code=400, detail="Unknown concept in this seed set.")
    payload["concepts"][req.concept_uri]["seeds"] = [
        {**item.model_dump(), "source": "manual"} for item in req.seeds
    ]
    save_seed_set(settings, name, payload)
    return {"updated": req.concept_uri, "count": len(req.seeds)}


@router.delete("/{name}", summary="Delete a seed set")
async def delete(name: SeedSetName, settings: Settings = Depends(get_settings)) -> dict:
    """Delete the seed set. Runs made from it keep their samples and exports but can no longer be resumed.

    Errors: 404 when no seed set has that name."""
    if not delete_seed_set(settings, name):
        raise HTTPException(status_code=404, detail=f"Seed set {name!r} not found.")
    return {"deleted": name}
