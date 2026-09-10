"""Table routes: a dataset as a TABLE, independent of any training schema.

Separate from ``refine.py`` on purpose. Every endpoint there presupposes text
columns and a label column -- that is the training-data layer. These endpoints
know only rows and columns, so they work on an arbitrary export before anyone
has decided which column is the label.

They share ``refine.py``'s store and its operation history, so a pipeline can
mix both kinds of step.

Mounted under the same ``/refine`` prefix as the endpoints it grew out of, so
existing clients keep working.
"""

from __future__ import annotations

import asyncio
from pathlib import Path

import pandas as pd
from fastapi import APIRouter, Depends, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..refine.apply import preview_or_apply, run_table_op
from ..refine.profile import profile_columns
from ..refine.store import load_dataset, save_dataset
from ..security import read_upload_capped, require_key, safe_name
from ..settings import Settings, get_settings
from ..tabular import SUPPORTED_READ, read_table, write_table

# Chosen so a browser saves rather than renders, and so a gzipped export is
# not silently decompressed by the transfer layer.
_MEDIA_TYPES = {
    "csv": "text/csv; charset=utf-8",
    "csv.gz": "application/gzip",
    "json": "application/json",
    "jsonl": "application/x-ndjson",
}

router = APIRouter(prefix="/refine", tags=["Refine"], dependencies=[Depends(require_key)])


@router.post("/datasets/import", summary="Upload a table (stored raw, not scrubbed)")
async def import_dataset(
    file: UploadFile,
    name: str | None = Form(default=None, max_length=100),
    format: str = Form(default="auto", max_length=20),
    separator: str = Form(default=";", max_length=3),
    encoding: str = Form(default="utf-8", max_length=20),
    list_separator: str = Form(default=",", max_length=3),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Import CSV, JSON or JSONL, plain or gzipped.

    ``format`` defaults to ``auto``: the extension names the format and gzip is
    detected from the bytes. Declare it explicitly to override a filename that
    lies. Nested JSON arrives flattened into dot-path columns.

    Whatever comes in is stored in the one internal shape (semicolon CSV,
    UTF-8), so no later operation has to know where the data came from.
    """
    payload = await read_upload_capped(file, settings.max_upload_mb * 1024 * 1024)
    resolved = safe_name(name or Path(file.filename or "dataset").stem, "dataset name")
    if format != "auto" and format not in SUPPORTED_READ:
        raise HTTPException(
            status_code=400,
            detail=f"Unsupported format {format!r}. Supported: auto, {', '.join(SUPPORTED_READ)}.",
        )
    try:
        df = read_table(
            payload, fmt=format, separator=separator, encoding=encoding,
            list_separator=list_separator, filename=file.filename or "",
        )
    except (ValueError, LookupError) as exc:
        # LookupError: an unknown encoding name is the caller's mistake, not ours.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    save_dataset(settings, resolved, df)
    return {"name": resolved, "rows": int(len(df)), "columns": list(df.columns)}


class OperationRequest(BaseModel):
    op: str = Field(max_length=40)
    params: dict = Field(default_factory=dict)
    target: str | None = Field(default=None, max_length=100)  # None = preview only


def _load_or_404(settings: Settings, name: str) -> pd.DataFrame:
    df = load_dataset(settings, safe_name(name, "dataset name"))
    if df is None:
        raise HTTPException(status_code=404, detail=f"Dataset {name!r} not found.")
    return df


@router.post("/{name}/op", summary="Preview (no target) or apply (target) a table operation")
async def run_operation(
    name: str, req: OperationRequest, settings: Settings = Depends(get_settings)
) -> dict:
    """Run one operation from :data:`TABLE_OPS` -- rules, or a column change.

    Without ``target`` nothing is written: a wrong filter must never cost the
    input. With one, the source's history is carried forward and this step
    appended, so a pipeline stays reconstructable.
    """
    df = _load_or_404(settings, name)
    target = safe_name(req.target, "target name") if req.target else None
    try:
        # Offload: these run over the whole frame, which would otherwise block
        # the event loop for health checks and progress polling.
        new_df, stats = await asyncio.to_thread(run_table_op, req.op, df, req.params, {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return preview_or_apply(settings, name, target, req.op, req.params, new_df, stats)


@router.get("/{name}/profile", summary="Per-column fill rate, cardinality and ranges")
async def profile_dataset(
    name: str,
    top_n: int = Query(default=10, ge=0, le=100),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Describe an unmapped table: what is in each column, before anyone has
    decided which one is the label."""
    df = _load_or_404(settings, name)
    return await asyncio.to_thread(profile_columns, df, top_n=top_n)


@router.get("/{name}/download", summary="Download the dataset in a chosen format")
async def download_dataset(
    name: str,
    format: str = Query(default="csv", max_length=20),
    separator: str = Query(default=";", max_length=3),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Export as CSV, CSV.gz, JSON or JSONL.

    The default is the semicolon CSV api_v3 reads, so the common case is one
    click with no options.
    """
    df = _load_or_404(settings, name)
    safe = safe_name(name, "dataset name")
    try:
        body = await asyncio.to_thread(write_table, df, fmt=format, separator=separator)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    return Response(
        content=body,
        media_type=_MEDIA_TYPES[format],
        # attachment, not inline: this is a file to save, and a browser would
        # otherwise render a multi-megabyte CSV as a wall of text.
        headers={"Content-Disposition": f'attachment; filename="{safe}.{format}"'},
    )
