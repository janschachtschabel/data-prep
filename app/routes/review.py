"""Review routes: filtered sample listing + per-sample status updates.

Regeneration reuses the run resume endpoint (routes/runs.py): discarding a
sample here reopens a slot that a subsequent resume refills.
"""

from __future__ import annotations

from typing import Literal

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel

from .. import run_store
from ..review import list_samples, update_status
from ..runs import run_manager
from ..security import require_key, safe_name
from ..settings import Settings, get_settings

router = APIRouter(prefix="/runs", tags=["Review"], dependencies=[Depends(require_key)])


class StatusUpdate(BaseModel):
    status: Literal["passed", "approved", "discarded"]


def _run_dir_or_404(settings: Settings, run_id: str):
    state = run_store.read_state(settings, safe_name(run_id, "run id"))
    if state is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found.")
    return settings.runs_dir / state["id"]


@router.get("/{run_id}/samples", summary="Browse a run's samples (filterable, paginated)")
async def samples(
    run_id: str,
    concept: str | None = Query(default=None, max_length=500),
    status: str | None = Query(default=None, max_length=30),
    min_sim: float | None = Query(default=None, ge=0.0, le=1.0),
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
    settings: Settings = Depends(get_settings),
) -> dict:
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
    safe_run = safe_name(run_id, "run id")
    if run_manager.is_active(safe_run):
        raise HTTPException(status_code=409, detail="Run is active — cancel it before editing samples.")
    run_dir = _run_dir_or_404(settings, safe_run)
    if not update_status(run_dir, safe_name(sample_id, "sample id"), body.status):
        raise HTTPException(status_code=404, detail=f"Sample {sample_id!r} not found.")
    return {"updated": sample_id, "status": body.status}
