"""Export routes: CSV/JSONL/audit downloads and the guarded api_v3 push.

Push guard: the configured api_v3 host must be on the fetch allowlist or be
localhost (dev). The API key comes from the env variable NAMED in config.yaml.
"""

from __future__ import annotations

import json

from fastapi import APIRouter, Depends, HTTPException, Query
from fastapi.responses import PlainTextResponse

from .. import run_store
from ..apiv3 import PushError, push_csv
from ..exporter import audit_markdown, load_samples, to_csv, to_jsonl
from ..security import require_key, safe_name
from ..settings import Settings, get_settings
from ..vocab import Vocabulary, parse_vocabulary
from .runs import RunId

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
async def export_csv(
    run_id: RunId,
    spreadsheet_safe: bool = Query(default=False, description=(
        "Write the variant for opening in Excel or LibreOffice: a cell that would run there as a "
        "formula gets an apostrophe in front and every field is quoted, and the file is offered as "
        "`<run_id>.spreadsheet.csv`. Not for api_v3: the apostrophe becomes part of the trained text.")),
    settings: Settings = Depends(get_settings),
) -> PlainTextResponse:
    """The run's `passed` and `approved` samples as a semicolon-separated UTF-8 CSV in the api_v3
    training schema: title, description and keyword columns, the concept's display name
    (`properties.ccm:taxonid_DISPLAYNAME`) and URI (`properties.ccm:taxonid`), `source=synthetic`, and
    `generated_for=<concept>` on every row -- the mark that keeps these rows out of any holdout this app
    splits and out of api_v3's validation. Reference data never reaches an export.

    Errors: 400 when the run's vocabulary is missing; 404 for an unknown run."""
    state, samples, vocab = _run_context(settings, run_id)
    # The defused file is named apart so it cannot be handed to api_v3 in place
    # of the training CSV -- two files called run-1.csv would be indistinguishable.
    name = f"{state['id']}.spreadsheet.csv" if spreadsheet_safe else f"{state['id']}.csv"
    return PlainTextResponse(
        to_csv(samples, vocab, spreadsheet_safe=spreadsheet_safe),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{name}"'},
    )


@router.get("/{run_id}/export.jsonl", summary="Download the run as canonical JSONL")
async def export_jsonl(run_id: RunId, settings: Settings = Depends(get_settings)) -> PlainTextResponse:
    """The run's `passed` and `approved` samples, one JSON object per line with every stored field (e.g.
    id, texts, concept, status, meta), each marked `generated_for=<concept>` like the CSV rows.

    Errors: 400 when the run's vocabulary is missing; 404 for an unknown run."""
    state, samples, _ = _run_context(settings, run_id)
    return PlainTextResponse(
        to_jsonl(samples),
        media_type="application/jsonl; charset=utf-8",
        headers={"Content-Disposition": f'attachment; filename="{state["id"]}.jsonl"'},
    )


@router.get("/{run_id}/audit.md", summary="Audit report (Markdown, German)")
async def audit(run_id: RunId, settings: Settings = Depends(get_settings)) -> PlainTextResponse:
    """A report for editors (German): run parameters, the mandatory usage notes (evaluate on curated
    data only), exportable samples against the target, LLM usage, diversity (mean and maximum
    `sim_max`), discards per reason and samples per concept.

    Errors: 400 when the run's vocabulary is missing; 404 for an unknown run."""
    state, samples, vocab = _run_context(settings, run_id)
    return PlainTextResponse(audit_markdown(state, samples, vocab),
                             media_type="text/markdown; charset=utf-8")


@router.post("/{run_id}/push", summary="Push the CSV export to the configured api_v3")
async def push(run_id: RunId, settings: Settings = Depends(get_settings)) -> dict:
    """Upload the CSV export as `<run_id>.csv` to `/datasets/import` of the api_v3 configured in
    config.yaml (`api_v3.url`), authenticated with the key from the env variable it names. The host must
    be on the fetch allowlist or localhost.

    Errors: 400 when the run has no exportable samples, its vocabulary is missing, or api_v3 is not
    configured, not allowed or has no key; 404 for an unknown run; 409 when api_v3 already has a dataset
    of that name; 502 when api_v3 refuses the upload or cannot be reached."""
    state, samples, vocab = _run_context(settings, run_id)
    if not samples:
        raise HTTPException(status_code=400, detail="This run has no exportable samples.")
    filename = f"{state['id']}.csv"
    try:
        body = await push_csv(settings, to_csv(samples, vocab), filename)
    except PushError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    return {"pushed": filename, "rows": len(samples), "api_v3": body}
