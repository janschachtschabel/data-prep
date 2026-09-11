"""Seed-set routes: build (distill or empty pools), list, detail, per-concept
LLM bootstrap, editor updates, delete."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from fastapi import APIRouter, Depends, HTTPException
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


class BuildRequest(BaseModel):
    name: str = Field(max_length=MAX_NAME_BYTES)
    vocab: str = Field(max_length=MAX_NAME_BYTES)
    reference: str | None = Field(default=None, max_length=MAX_NAME_BYTES)
    # Few-shot example seeds per concept (5-10 is plenty); this is NOT the term
    # bank scope, which is controlled separately below.
    per_concept: int = Field(default=6, ge=1, le=50)
    # Term bank: which reference columns feed it (None → the reference's configured
    # text columns), how many rows per label to mine, and the max distinct terms.
    keyword_columns: list[str] | None = None
    term_columns: list[str] | None = None
    terms_max_rows: int = Field(default=500, ge=0, le=20000)
    terms_top_n: int = Field(default=60, ge=10, le=500)
    # Rebuilding discards every hand-edited and LLM-bootstrapped seed of the set.
    overwrite: bool = False


class BootstrapRequest(BaseModel):
    concept_uri: str = Field(max_length=500)
    n: int = Field(default=4, ge=1, le=20)


class RefineTermsRequest(BaseModel):
    concept_uri: str = Field(max_length=500)
    n: int = Field(default=30, ge=1, le=100)
    context: str = Field(default="", max_length=2000)


class ConceptSeedsPut(BaseModel):
    concept_uri: str = Field(max_length=500)
    seeds: list[SeedItem]


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
    return {"seed_sets": list_seed_sets(settings)}


@router.post("/build", summary="Build a seed set (distilled from a reference, or empty pools)")
async def build(req: BuildRequest, settings: Settings = Depends(get_settings)) -> dict:
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
async def detail(name: str, settings: Settings = Depends(get_settings)) -> dict:
    return _load_or_404(settings, name)


@router.post("/{name}/bootstrap", summary="LLM-generate seeds for ONE concept (vocab-only anchor)")
async def bootstrap(
    name: str, req: BootstrapRequest,
    settings: Settings = Depends(get_settings),
    override: LlmOverride = Depends(llm_override),
) -> dict:
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
    name: str, req: RefineTermsRequest,
    settings: Settings = Depends(get_settings),
    override: LlmOverride = Depends(llm_override),
) -> dict:
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
async def update_concept(name: str, req: ConceptSeedsPut, settings: Settings = Depends(get_settings)) -> dict:
    payload = _load_or_404(settings, name)
    if req.concept_uri not in payload["concepts"]:
        raise HTTPException(status_code=400, detail="Unknown concept in this seed set.")
    payload["concepts"][req.concept_uri]["seeds"] = [
        {**item.model_dump(), "source": "manual"} for item in req.seeds
    ]
    save_seed_set(settings, name, payload)
    return {"updated": req.concept_uri, "count": len(req.seeds)}


@router.delete("/{name}", summary="Delete a seed set")
async def delete(name: str, settings: Settings = Depends(get_settings)) -> dict:
    if not delete_seed_set(settings, name):
        raise HTTPException(status_code=404, detail=f"Seed set {name!r} not found.")
    return {"deleted": name}
