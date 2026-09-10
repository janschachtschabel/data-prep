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

from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile

from ..refine.store import save_dataset
from ..security import read_upload_capped, require_key, safe_name
from ..settings import Settings, get_settings
from ..tabular import SUPPORTED_READ, read_table

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
