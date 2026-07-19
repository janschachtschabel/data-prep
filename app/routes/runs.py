"""Run routes: dry-run plan, start, list, status, cancel, resume."""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import run_store
from ..config import load_config
from ..llm import LlmOverride
from ..planning import build_plan, estimate, resolve_corridor
from ..runs import run_manager
from ..security import llm_override, require_key, safe_name
from ..seeds import load_seed_set
from ..settings import Settings, get_settings
from ..vocab import Vocabulary, parse_vocabulary

router = APIRouter(prefix="/runs", tags=["Runs"], dependencies=[Depends(require_key)])


class Selection(BaseModel):
    mode: Literal["all", "subtree", "list"] = "all"
    root: str | None = Field(default=None, max_length=500)
    uris: list[str] | None = None


class LengthProfileIn(BaseModel):
    name: str | None = Field(default=None, max_length=30)
    min: int | None = Field(default=None, ge=20, le=20000)
    max: int | None = Field(default=None, ge=50, le=50000)


class RunRequest(BaseModel):
    seed_set: str = Field(max_length=100)
    selection: Selection = Selection()
    per_concept: int = Field(default=50, ge=1, le=2000)
    batch_size: int = Field(default=8, ge=1, le=20)
    length_profile: LengthProfileIn = LengthProfileIn()
    # Optional free-text steering for the generation prompt (what the entries are
    # about). Empty keeps the default catalog framing.
    guidance: str = Field(default="", max_length=2000)
    # Concurrent LLM calls for this run (None → server default). Effective
    # parallelism is min(concurrency, number of selected concepts).
    concurrency: int | None = Field(default=None, ge=1, le=64)


def _resolve(req: RunRequest, settings: Settings) -> tuple[dict, Vocabulary, dict]:
    """Load seed set + vocabulary and build the deterministic plan (shared by
    the dry run and the actual start)."""
    seed_payload = load_seed_set(settings, safe_name(req.seed_set, "seed set name"))
    if seed_payload is None:
        raise HTTPException(status_code=404, detail=f"Seed set {req.seed_set!r} not found.")
    import json as _json

    vocab_path = settings.data_dir / "vocabs" / f"{safe_name(seed_payload['vocab'], 'vocabulary name')}.json"
    if not vocab_path.exists():
        raise HTTPException(status_code=400, detail=f"Vocabulary {seed_payload['vocab']!r} not found.")
    vocab = parse_vocabulary(_json.loads(vocab_path.read_text(encoding="utf-8")))
    try:
        plan = build_plan(seed_payload, vocab, req.selection.model_dump(), per_concept=req.per_concept)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return seed_payload, vocab, {"plan": plan}


@router.post("/plan", summary="Dry run: plan + cost estimate (no LLM call, nothing stored)")
async def dry_run(req: RunRequest, settings: Settings = Depends(get_settings)) -> dict:
    _, _, resolved = _resolve(req, settings)
    try:
        profile_name, corridor = resolve_corridor(req.length_profile.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    cfg = load_config(settings.config_file)
    return {
        "plan": resolved["plan"],
        "estimate": estimate(resolved["plan"], batch_size=req.batch_size),
        "length_profile": profile_name,
        "corridor": list(corridor),
        "budgets": cfg.budgets.model_dump(),
    }


@router.post("", summary="Start a generation run (one active run per server)")
async def start_run(
    req: RunRequest,
    settings: Settings = Depends(get_settings),
    override: LlmOverride = Depends(llm_override),
) -> dict:
    seed_payload, _, resolved = _resolve(req, settings)
    try:
        profile_name, corridor = resolve_corridor(req.length_profile.model_dump())
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    concepts = [item["concept"] for item in resolved["plan"]]
    params = {
        "seed_set": req.seed_set,
        "vocab": seed_payload["vocab"],
        "concepts": concepts,
        "per_concept": req.per_concept,
        "batch_size": req.batch_size,
        "length_profile": profile_name,
        "corridor": list(corridor),
        "guidance": req.guidance,
        "concurrency": req.concurrency,
        "target": len(concepts) * req.per_concept,
    }
    try:
        run_id = run_manager.start(settings, params, override)
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"run_id": run_id}


@router.get("", summary="List runs (newest first, optionally capped)")
async def list_runs(
    limit: int | None = Query(default=None, ge=1, le=1000),
    settings: Settings = Depends(get_settings),
) -> dict:
    runs = run_store.list_runs(settings)
    return {"runs": runs[:limit] if limit else runs, "total": len(runs)}


@router.get("/{run_id}", summary="Run status with live counters")
async def run_status(run_id: str, settings: Settings = Depends(get_settings)) -> dict:
    state = run_store.read_state(settings, safe_name(run_id, "run id"))
    if state is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found.")
    return state


@router.post("/{run_id}/cancel", summary="Cancel the active run (resumable later)")
async def cancel_run(run_id: str, settings: Settings = Depends(get_settings)) -> dict:
    if not run_manager.cancel(safe_name(run_id, "run id")):
        raise HTTPException(status_code=409, detail="This run is not currently active.")
    return {"cancelling": run_id}


@router.post("/{run_id}/resume", summary="Resume a paused/cancelled/failed run")
async def resume_run(
    run_id: str,
    settings: Settings = Depends(get_settings),
    override: LlmOverride = Depends(llm_override),
) -> dict:
    try:
        run_manager.resume(settings, safe_name(run_id, "run id"), override)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"resumed": run_id}
