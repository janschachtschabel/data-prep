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

from ..refine.store import read_csv, save_dataset
from ..security import read_upload_capped, require_key, safe_name
from ..settings import Settings, get_settings

router = APIRouter(prefix="/refine", tags=["Refine"], dependencies=[Depends(require_key)])


@router.post("/datasets/import", summary="Upload a working CSV (stored raw, not scrubbed)")
async def import_dataset(
    file: UploadFile,
    name: str | None = Form(default=None, max_length=100),
    settings: Settings = Depends(get_settings),
) -> dict:
    payload = await read_upload_capped(file, settings.max_upload_mb * 1024 * 1024)
    resolved = safe_name(name or Path(file.filename or "dataset").stem, "dataset name")
    try:
        df = read_csv(payload)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    save_dataset(settings, resolved, df)
    return {"name": resolved, "rows": int(len(df)), "columns": list(df.columns)}
