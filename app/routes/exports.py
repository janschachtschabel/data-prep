"""Export routes: CSV/JSONL/audit downloads and the guarded api_v3 push.

Push guard: the configured api_v3 host must be on the fetch allowlist or be
localhost (dev). The API key comes from the env variable NAMED in config.yaml.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException
from fastapi.responses import PlainTextResponse

from .. import run_store
from ..apiv3 import PushError, push_csv
from ..exporter import audit_markdown, load_samples, to_csv, to_jsonl
from ..security import require_key, safe_name
from ..settings import Settings, get_settings
from ..vocab import Vocabulary, parse_vocabulary

router = APIRouter(prefix="/runs", tags=["Runs"], dependencies=[Depends(require_key)])


def _run_context(settings: Settings, run_id: str) -> tuple[dict, list[dict], Vocabulary]:
    state = run_store.read_state(settings, safe_name(run_id, "run id"))
    if state is None:
        raise HTTPException(status_code=404, detail=f"Run {run_id!r} not found.")
    samples = load_samples(settings.runs_dir / state["id"])
    vocab_path = settings.data_dir / "vocabs" / f"{safe_name(state['params']['vocab'], 'vocabulary name')}.json"
    if not vocab_path.exists():
        raise HTTPException(status_code=400, detail=f"Vocabulary {state['params']['vocab']!r} not found.")
    vocab = parse_vocabulary(json.loads(vocab_path.read_text(encoding="utf-8")))
    return state, samples, vocab


@router.get("/{run_id}/export.csv", summary="Download the run as api_v3 training CSV")
async def export_csv(run_id: str, settings: Settings = Depends(get_settings)) -> PlainTextResponse:
    state, samples, vocab = _run_context(settings, run_id)
    return PlainTextResponse(
        to_csv(samples, vocab),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{state["id"]}.csv"'},
    )


@router.get("/{run_id}/export.jsonl", summary="Download the run as canonical JSONL")
async def export_jsonl(run_id: str, settings: Settings = Depends(get_settings)) -> PlainTextResponse:
    state, samples, _ = _run_context(settings, run_id)
    return PlainTextResponse(
        to_jsonl(samples),
        media_type="application/jsonl; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{state["id"]}.jsonl"'},
    )


@router.get("/{run_id}/audit.md", summary="Audit report (Markdown, German)")
async def audit(run_id: str, settings: Settings = Depends(get_settings)) -> PlainTextResponse:
    state, samples, vocab = _run_context(settings, run_id)
    return PlainTextResponse(audit_markdown(state, samples, vocab),
                             media_type="text/markdown; charset=utf-8")


@router.post("/{run_id}/push", summary="Push the CSV export to the configured api_v3")
async def push(run_id: str, settings: Settings = Depends(get_settings)) -> dict:
    state, samples, vocab = _run_context(settings, run_id)
    if not samples:
        raise HTTPException(status_code=400, detail="This run has no exportable samples.")
    filename = f"{state['id']}.csv"
    try:
        body = await push_csv(settings, to_csv(samples, vocab), filename)
    except PushError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    return {"pushed": filename, "rows": len(samples), "api_v3": body}
