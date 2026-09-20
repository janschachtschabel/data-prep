"""Reference-set routes: import (upload with input scrub), list, detail, delete.

Stored per set: ``<name>.csv`` (scrubbed) + ``<name>.meta.json`` (columns, PII
report, group counts). Listing keys off the meta files.
"""

from __future__ import annotations

import asyncio
import json
from pathlib import Path
from typing import Annotated

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi import Path as PathParam

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

# The reference-set name both detail routes take in their path.
ReferenceName = Annotated[str, PathParam(description=(
    "A reference set as `GET /references` lists it. A plain name: path characters, or more than "
    "200 bytes, are refused with 400; a name no set has answers 404."))]


def _paths(settings: Settings, name: str) -> tuple[Path, Path]:
    safe = safe_name(name, "reference name")
    base = references_dir(settings)
    return base / f"{safe}.csv", base / f"{safe}.meta.json"


@router.get("", summary="List reference sets")
async def list_references(settings: Settings = Depends(get_settings)) -> dict:
    """Every stored reference set: name, row count, number of distinct labels, rows the PII scrub
    changed, and the text and label columns it was imported with."""
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
    file: UploadFile = File(description="Semicolon-separated UTF-8 CSV with the text and label columns."),
    name: str | None = Form(default=None, max_length=MAX_NAME_BYTES, description=(
        "Name to store the reference set under; default: the file name without its extension.")),
    text_columns: str | None = Form(default=None, max_length=500, description=(
        "Comma-separated text columns in the order title, description, keywords: seeds and the leakage "
        "check read them by position. Default: `properties.cclom:title`, "
        "`properties.cclom:general_description`, `properties.cclom:general_keyword`.")),
    label_column: str | None = Form(default=None, max_length=100, description=(
        "Column with each row's concept URIs, several separated by `,`; default: `properties.ccm:taxonid`.")),
    overwrite: bool = Form(default=False, description=(
        "Replace an existing reference set of that name; without it the name is refused with 409.")),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Import a curated CSV as input for hybrid seed sets (seeds, term banks) and the leakage check of
    their runs; it never reaches an export. The text columns are PII-scrubbed in memory before anything
    is written (e-mail, URL, phone and handle masked; person names are not detected); every other
    column, the label column included, is stored unchanged. Returns the stored meta: row count, columns,
    PII report and rows per label.

    Errors: 400 for an unreadable CSV, missing columns or an invalid name; 409 when the name is taken and
    `overwrite` is not set; 413 when the file exceeds the upload cap (DATAPREP_MAX_UPLOAD_MB)."""
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
async def reference_detail(name: ReferenceName, settings: Settings = Depends(get_settings)) -> dict:
    """The stored meta of a reference set: row count, text and label columns, PII report and the number
    of rows per label.

    Errors: 404 when no reference set has that name."""
    _, meta_path = _paths(settings, name)
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail=f"Reference set {name!r} not found.")
    return json.loads(meta_path.read_text(encoding="utf-8"))


@router.delete("/{name}", summary="Delete a reference set")
async def delete_reference(name: ReferenceName, settings: Settings = Depends(get_settings)) -> dict:
    """Delete the reference set. Seed sets distilled from it keep their seeds, but their runs fail on
    start or resume: the leakage check needs the reference. A default reference named in config.yaml is
    imported again from its source file at the next server start.

    Errors: 404 when no reference set has that name."""
    csv_path, meta_path = _paths(settings, name)
    if not meta_path.exists():
        raise HTTPException(status_code=404, detail=f"Reference set {name!r} not found.")
    meta_path.unlink()
    csv_path.unlink(missing_ok=True)
    return {"deleted": name}
