"""Reference-set routes: import (upload with input scrub), list, detail, delete.

Stored per set: ``<name>.csv`` (scrubbed) + ``<name>.meta.json`` (columns, PII
report, group counts). Listing keys off the meta files.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path

from fastapi import APIRouter, Depends, Form, HTTPException, UploadFile

from ..reference import (
    DEFAULT_LABEL_COLUMN,
    DEFAULT_TEXT_COLUMNS,
    ingest_reference,
    references_dir,
    store_reference,
)
from ..security import MAX_NAME_BYTES, read_upload_capped, refuse_existing, require_key, safe_name
from ..settings import Settings, get_settings

router = APIRouter(prefix="/references", tags=["References"], dependencies=[Depends(require_key)])


def _paths(settings: Settings, name: str) -> tuple[Path, Path]:
    safe = safe_name(name, "reference name")
    base = references_dir(settings)
    return base / f"{safe}.csv", base / f"{safe}.meta.json"


@router.get("", summary="List reference sets")
async def list_references(settings: Settings = Depends(get_settings)) -> dict:
    references = []
    for meta_path in sorted(references_dir(settings).glob("*.meta.json")):
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        references.append(
            {
                "name": meta["name"],
                "row_count": meta["row_count"],
                "group_count": len(meta["group_counts"]),
                "pii_rows_affected": meta["pii"]["rows_affected"],
                # Which columns become title/description/keywords and the label —
                # so the UI can show the field mapping (no field is guessed).
                "text_columns": meta["text_columns"],
                "label_column": meta["label_column"],
            }
        )
    return {"references": references}


@router.post("/import", summary="Upload a reference CSV (input PII scrub applied)")
async def import_reference(
    file: UploadFile,
    name: str | None = Form(default=None, max_length=MAX_NAME_BYTES),
    text_columns: str | None = Form(default=None, max_length=500),
    label_column: str | None = Form(default=None, max_length=100),
    overwrite: bool = Form(default=False),
    settings: Settings = Depends(get_settings),
) -> dict:
    payload = await read_upload_capped(file, settings.max_upload_mb * 1024 * 1024)
    resolved = name or Path(file.filename or "reference").stem
    columns = (
        tuple(part.strip() for part in text_columns.split(",") if part.strip())
        if text_columns
        else DEFAULT_TEXT_COLUMNS
    )
    safe = safe_name(resolved, "reference name")  # may 400 — before any work
    # The meta file is what makes a reference set exist (listing, detail).
    refuse_existing(_paths(settings, safe)[1].exists(), "Reference set", safe, overwrite)
    try:
        # Parsing plus the per-cell PII scrub of an upload this size is CPU work;
        # inline it would block every other request on the single loop.
        df, meta = await asyncio.to_thread(
            ingest_reference,
            payload, name=resolved, text_columns=columns, label_column=label_column or DEFAULT_LABEL_COLUMN,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    try:
        # Check and write under the store lock: the startup import of a default
        # reference may have claimed the name while this upload was scrubbed.
        store_reference(settings, safe, df, meta, replace=overwrite)
    except FileExistsError:
        refuse_existing(True, "Reference set", safe, overwrite=False)
    return meta


@router.get("/{name}", summary="Reference set details (groups, PII report)")
async def reference_detail(name: str, settings: Settings = Depends(get_settings)) -> dict:
    _, meta_path = _paths(settings, name)
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail=f"Reference set {name!r} not found.")
    return json.loads(meta_path.read_text(encoding="utf-8"))


@router.delete("/{name}", summary="Delete a reference set")
async def delete_reference(name: str, settings: Settings = Depends(get_settings)) -> dict:
    csv_path, meta_path = _paths(settings, name)
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail=f"Reference set {name!r} not found.")
    meta_path.unlink()
    csv_path.unlink(missing_ok=True)
    return {"deleted": name}
