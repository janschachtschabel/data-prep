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
from ..security import MAX_NAME_BYTES, llm_override, require_key, safe_name
from ..seeds import load_seed_set
from ..settings import Settings, get_settings
from ..vocab import Vocabulary, parse_vocabulary

router = APIRouter(prefix="/runs", tags=["Runs"], dependencies=[Depends(require_key)])


class Selection(BaseModel):
    mode: Literal["all", "subtree", "list"] = Field(default="all", description=(
        "`all`: every concept of the vocabulary; `subtree`: `root` and everything below it; `list`: the "
        "concepts in `uris`. Concepts are always taken in vocabulary tree order."))
    root: str | None = Field(default=None, max_length=500, description=(
        "Concept URI whose subtree is selected (mode `subtree`); must be in the vocabulary."))
    uris: list[str] | None = Field(default=None, description=(
        "Concept URIs to select (mode `list`); every one must be in the vocabulary."))


class LengthProfileIn(BaseModel):
    name: str | None = Field(default=None, max_length=30, description=(
        "Description length in characters: `kurz` (120-300), `standard` (300-600), `lang` (800-2000) or "
        "`volltext` (3000-6000). Any other name uses `min`/`max`; omitted, `standard` unless `min` or "
        "`max` is set."))
    min: int | None = Field(default=None, ge=20, le=20000, description=(
        "Custom corridor: minimum description length in characters (default 100)."))
    max: int | None = Field(default=None, ge=50, le=50000, description=(
        "Custom corridor: maximum description length in characters (default 600); must exceed `min`."))


class RunRequest(BaseModel):
    seed_set: str = Field(max_length=MAX_NAME_BYTES, description=(
        "Seed set to generate from; its vocabulary supplies the concepts."))
    selection: Selection = Field(default=Selection(), description="Which concepts to generate for.")
    per_concept: int = Field(default=50, ge=1, le=2000, description=(
        "Samples to keep per selected concept (after the gates)."))
    batch_size: int = Field(default=8, ge=1, le=20, description="Samples requested per LLM call.")
    length_profile: LengthProfileIn = Field(default=LengthProfileIn(), description=(
        "Allowed description length; generated samples outside it are discarded."))
    # Optional free-text steering for the generation prompt (what the entries are
    # about). Empty keeps the default catalog framing.
    guidance: str = Field(default="", max_length=2000, description=(
        "Optional instruction added to every generation prompt (what the entries are about); empty keeps "
        "the default catalog framing."))
    # Concurrent LLM calls for this run (None → server default). Effective
    # parallelism is min(concurrency, number of selected concepts).
    concurrency: int | None = Field(default=None, ge=1, le=64, description=(
        "Concurrent LLM calls; omitted = the server default (DATAPREP_GENERATION_CONCURRENCY). A "
        "concept's batches run one after another, so at most one call per selected concept runs at once."))


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
    """Resolve the concept selection and the length corridor and estimate the cost, without calling the
    LLM or storing anything. Returns the plan (per concept: label, available seeds, target), the estimate
    (total target, LLM calls, and tokens at a rough 2,000 per call), the resolved length profile and
    corridor, and the configured per-run budgets.

    Errors: 400 when the seed set's vocabulary is missing, the selection names unknown concepts, or `min`
    is not below `max`; 404 for an unknown seed set."""
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
    """Start generating in the background and return the new `run_id` at once; poll `GET /runs/{run_id}`.
    Each batch prompt names the concept and its place in the hierarchy, a rotating audience or angle,
    up to four seeds as examples, part of the term bank and titles to avoid. Samples are PII-scrubbed
    and discarded when outside the length corridor, an exact or semantic duplicate, or (with a
    reference) too close to a reference text. Paid LLM calls on the `bulk` endpoint; honours
    `X-LLM-Key`/`X-LLM-Model`. A budget cap pauses the run; a missing LLM key fails it (status
    `failed`), not this request.

    Errors: as the dry run, plus 409 while another run is active."""
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
    limit: int | None = Query(default=None, ge=1, le=1000, description=(
        "Return at most this many runs, newest first; omitted = all. `total` always counts every run.")),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Run summaries, newest first: id, status (`running`, `paused`, `cancelled`, `failed`, `completed` or
    `interrupted`), creation time, samples kept, target and the last message."""
    runs = run_store.list_runs(settings)
    return {"runs": runs[:limit] if limit else runs, "total": len(runs)}


@router.get("/{run_id}", summary="Run status with live counters")
async def run_status(run_id: str, settings: Settings = Depends(get_settings)) -> dict:
    """The run's full state: its parameters (with the model actually used), status and message, live
    counters (samples kept per concept, discards per reason, failed batches) and LLM usage.

    Errors: 404 for an unknown run."""
    state = run_store.read_state(settings, safe_name(run_id, "run id"))
    if state is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found.")
    return state


@router.post("/{run_id}/cancel", summary="Cancel the active run (resumable later)")
async def cancel_run(run_id: str, settings: Settings = Depends(get_settings)) -> dict:
    """Ask the active run to stop. Batches already sent finish and are kept; then the run ends as
    `cancelled` and can be resumed.

    Errors: 409 when this run is not the active one."""
    if not run_manager.cancel(safe_name(run_id, "run id")):
        raise HTTPException(status_code=409, detail="This run is not currently active.")
    return {"cancelling": run_id}


@router.post("/{run_id}/resume", summary="Resume a paused, cancelled, failed, interrupted or completed run")
async def resume_run(
    run_id: str,
    settings: Settings = Depends(get_settings),
    override: LlmOverride = Depends(llm_override),
) -> dict:
    """Continue the run in the background up to its target. Kept samples still count, and every earlier
    sample, kept or discarded, stays in the duplicate checks -- so a resume refills only the open slots,
    including those opened by discarding samples in review. The LLM key is never stored: send
    `X-LLM-Key` again unless the server has one.

    Errors: 400 when the run is not in a resumable state; 404 for an unknown run; 409 while a run is
    active."""
    try:
        run_manager.resume(settings, safe_name(run_id, "run id"), override)
    except KeyError as exc:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found.") from exc
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(status_code=409, detail=str(exc)) from exc
    return {"resumed": run_id}
