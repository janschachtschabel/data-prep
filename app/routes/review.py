"""Review routes: filtered sample listing + per-sample status updates.

Regeneration reuses the run resume endpoint (routes/runs.py): discarding a
sample here reopens a slot that a subsequent resume refills.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel, Field

from .. import run_store
from ..review import list_samples, update_status
from ..runs import run_manager
from ..security import require_key, safe_name
from ..settings import Settings, get_settings

router = APIRouter(prefix="/runs", tags=["Review"], dependencies=[Depends(require_key)])


class StatusUpdate(BaseModel):
    status: Literal["passed", "approved", "discarded"] = Field(description=(
        "`approved` (editor confirmed), `discarded` (editor rejected: not exported, and its slot is "
        "refilled by the next resume) or `passed` (back to the automated verdict)."))


def _run_dir_or_404(settings: Settings, run_id: str):
    state = run_store.read_state(settings, safe_name(run_id, "run id"))
    if state is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found.")
    return settings.runs_dir / state["id"]


@router.get("/{run_id}/samples", summary="Browse a run's samples (filterable, paginated)")
async def samples(
    run_id: str,
    concept: str | None = Query(default=None, max_length=500, description=(
        "Only samples generated for this concept URI.")),
    status: str | None = Query(default=None, max_length=30, description=(
        "Only samples with this status: `passed`, `approved` or `discarded`.")),
    min_sim: float | None = Query(default=None, ge=0.0, le=1.0, description=(
        "Only samples whose highest cosine similarity to an earlier sample or a reference text "
        "(`meta.sim_max`) is at least this -- the likeliest near-duplicates.")),
    offset: int = Query(default=0, ge=0, description="Matching samples to skip (paging)."),
    limit: int = Query(default=50, ge=1, le=500, description="Page size: samples returned at most."),
    settings: Settings = Depends(get_settings),
) -> dict:
    """One page of the run's samples in generation order, filtered by concept, status and minimum
    similarity; `total` counts every match before paging. Each sample carries its `id`, texts, concept,
    status and meta (model, batch, `sim_max`).

    Errors: 404 for an unknown run."""
    run_dir = _run_dir_or_404(settings, run_id)
    return list_samples(
        run_dir, concept=concept, status=status, min_sim=min_sim, offset=offset, limit=limit
    )


@router.post("/{run_id}/samples/{sample_id}/status", summary="Approve/discard/reset one sample")
async def set_status(
    run_id: str,
    sample_id: str,
    body: StatusUpdate,
    settings: Settings = Depends(get_settings),
) -> dict:
    """Set one sample's review status (`sample_id` is the `id` from the sample list). Only `passed` and
    `approved` samples are exported; a discarded one stays on file, so a resume never generates it again
    but refills its slot.

    Errors: 404 for an unknown run or sample; 409 while the run is active (cancel it first)."""
    safe_run = safe_name(run_id, "run id")
    if run_manager.is_active(safe_run):
        raise HTTPException(status_code=409, detail="Run is active — cancel it before editing samples.")
    run_dir = _run_dir_or_404(settings, safe_run)
    if not update_status(run_dir, safe_name(sample_id, "sample id"), body.status):
        raise HTTPException(status_code=404, detail=f"Sample {sample_id!r} not found.")
    return {"updated": sample_id, "status": body.status}
