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
from ..refine.duplicates import duplicate_report
from ..refine.join import DEFAULT_MAX_ROWS, join_datasets, key_cardinality
from ..refine.profile import profile_columns
from ..refine.store import load_dataset, save_dataset, write_ops
from ..refine.view import page_rows
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
        # Offloaded like the operations below: inflating and parsing up to
        # max_bytes inline would stall /health and run polling for the duration.
        df = await asyncio.to_thread(
            read_table,
            payload, fmt=format, separator=separator, encoding=encoding,
            list_separator=list_separator, filename=file.filename or "",
            # Ten times the compressed cap: real exports inflate 5-10x, a bomb
            # a thousandfold. The setting an operator already tunes bounds both.
            max_bytes=settings.max_upload_mb * 1024 * 1024 * 10,
        )
    except (ValueError, LookupError) as exc:
        # LookupError: an unknown encoding name is the caller's mistake, not ours.
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    save_dataset(settings, resolved, df)
    # A fresh import has no steps yet; without this, re-importing under a used
    # name kept the previous table's history.
    write_ops(settings, resolved, [])
    return {"name": resolved, "rows": int(len(df)), "columns": list(df.columns)}


class JoinRequest(BaseModel):
    right: str = Field(max_length=100)
    keys: list[dict] = Field(default_factory=list)
    how: str = Field(default="left", max_length=10)
    suffix: str = Field(default="_right", max_length=20)
    coalesce: bool = False
    target: str | None = Field(default=None, max_length=100)  # None = report only


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


@router.get("/{name}/duplicates", summary="How many rows share a key, and which")
async def duplicates(
    name: str,
    keys: list[str] = Query(default=[], description="Key columns; repeat for a composite key"),
    examples: int = Query(default=5, ge=0, le=50),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Non-destructive count of rows sharing a key.

    ``removable_rows`` is exactly what the ``dedupe_keys`` operation would drop,
    so the report and the removal can never disagree.
    """
    df = _load_or_404(settings, name)
    try:
        return await asyncio.to_thread(duplicate_report, df, list(keys), examples=examples)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{name}/join", summary="Report a join (no target) or run it (target)")
async def join(name: str, req: JoinRequest, settings: Settings = Depends(get_settings)) -> dict:
    """Match ``right`` onto this dataset over one or more keys.

    Without a target this reports the CARDINALITY rather than a materialised
    result. That is the honest preview for a join: what an operator needs to
    know first is how many rows it would produce, and a key that repeats on both
    sides can multiply them beyond memory. Computing that from the key counts
    costs nothing, while materialising it is the very thing worth avoiding.
    """
    left = _load_or_404(settings, name)
    right = _load_or_404(settings, req.right)
    target = safe_name(req.target, "target name") if req.target else None
    try:
        if target is None:
            report = await asyncio.to_thread(key_cardinality, left, right, req.keys)
            return {**report, "preview": True}
        new_df, stats = await asyncio.to_thread(
            join_datasets, left, right, keys=req.keys, how=req.how,
            suffix=req.suffix, coalesce=req.coalesce, max_rows=DEFAULT_MAX_ROWS,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    params = {"right": req.right, "keys": req.keys, "how": req.how,
              "suffix": req.suffix, "coalesce": req.coalesce}
    return preview_or_apply(settings, name, target, "join", params, new_df, stats)


@router.get("/{name}/rows", summary="Browse rows, paginated and searchable")
async def rows(
    name: str,
    offset: int = Query(default=0, ge=0),
    limit: int = Query(default=50, ge=1, le=500),
    q: str = Query(default="", max_length=200),
    columns: list[str] = Query(default=[], description="Restrict to these columns"),
    settings: Settings = Depends(get_settings),
) -> dict:
    """One page of rows, with an optional case-insensitive substring search.

    Only the requested page is serialised: a 400k-row table would otherwise be
    turned into a single response. ``total`` and ``matched`` are both reported,
    because "30 of 60" is how an operator sees that a search did what they meant.
    """
    df = _load_or_404(settings, name)
    try:
        return await asyncio.to_thread(
            page_rows, df, offset=offset, limit=limit, query=q, columns=list(columns) or None
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
