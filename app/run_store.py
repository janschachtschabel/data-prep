"""Run persistence: the authoritative ``state.json`` per run (atomic write),
run listing, and crash reconciliation.

Split from the run worker (:class:`app.runs.RunManager`) so on-disk state and
orchestration are separate concerns. Stateless, process-local (single-worker
design): every function takes ``settings`` and touches only the runs directory.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

from .settings import Settings


def run_dir(settings: Settings, run_id: str) -> Path:
    return settings.runs_dir / run_id


def read_state(settings: Settings, run_id: str) -> dict | None:
    path = run_dir(settings, run_id) / "state.json"
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def write_state(settings: Settings, state: dict) -> None:
    path = run_dir(settings, state["id"]) / "state.json"
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def list_runs(settings: Settings) -> list[dict]:
    summaries = []
    if settings.runs_dir.exists():
        for state_path in settings.runs_dir.glob("*/state.json"):
            state = json.loads(state_path.read_text(encoding="utf-8"))
            summaries.append(
                {
                    "id": state["id"],
                    "status": state["status"],
                    "created_at": state["created_at"],
                    "generated": state["counters"]["generated"],
                    "target": state["params"]["target"],
                    "message": state.get("message"),
                }
            )
    return sorted(summaries, key=lambda s: s["created_at"], reverse=True)


def reconcile_interrupted(settings: Settings) -> int:
    """At startup no run is live yet, so any run still marked 'running' in its
    state file is a leftover from a crash or restart. Mark those 'interrupted'
    so they are clearly not active and can be resumed — instead of sitting
    forever as a fake 'running' the UI can neither cancel nor resume. Returns
    how many were fixed."""
    fixed = 0
    if not settings.runs_dir.exists():
        return 0
    for state_path in settings.runs_dir.glob("*/state.json"):
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            continue
        if state.get("status") == "running":
            state["status"] = "interrupted"
            state["message"] = "Interrupted by a server restart — resume to continue."
            tmp = state_path.with_suffix(".json.tmp")
            tmp.write_text(json.dumps(state, ensure_ascii=False), encoding="utf-8")
            os.replace(tmp, state_path)
            fixed += 1
    return fixed
