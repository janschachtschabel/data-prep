"""Review (M9): browse a run's samples with filters and change per-sample status.

Statuses are the editorial gate before export: ``passed`` (survived the
automated gates), ``approved`` (editor confirmed), ``discarded`` (editor
rejected). ``samples.jsonl`` is rewritten atomically; discarded rows STAY on
disk so a later resume never regenerates them (the worker's exact-dedupe and
avoid-title context covers every row, kept or discarded).
"""

from __future__ import annotations

import json
from pathlib import Path

from .atomic import write_text_atomic
from .samples import read_samples

REVIEW_STATUSES = ("passed", "approved", "discarded")


def _read_all(run_dir: Path) -> list[dict]:
    return read_samples(run_dir / "samples.jsonl")


def list_samples(
    run_dir: Path,
    *,
    concept: str | None = None,
    status: str | None = None,
    min_sim: float | None = None,
    offset: int = 0,
    limit: int = 50,
) -> dict:
    """Filtered, paginated view of a run's samples (total is the pre-page count)."""
    samples = _read_all(run_dir)
    if concept:
        samples = [s for s in samples if s.get("concept") == concept]
    if status:
        samples = [s for s in samples if s.get("status") == status]
    if min_sim is not None:
        samples = [s for s in samples if s.get("meta", {}).get("sim_max", 0.0) >= min_sim]
    return {"total": len(samples), "offset": offset, "samples": samples[offset:offset + limit]}


def update_status(run_dir: Path, sample_id: str, new_status: str) -> bool:
    """Set one sample's status and rewrite the file atomically; ``False`` if the
    sample id is unknown."""
    path = run_dir / "samples.jsonl"
    if not path.exists():
        return False
    samples = _read_all(run_dir)
    found = False
    for sample in samples:
        if sample.get("id") == sample_id:
            sample["status"] = new_status
            found = True
    if not found:
        return False
    write_text_atomic(path, "\n".join(json.dumps(s, ensure_ascii=False) for s in samples) + "\n")
    return True
