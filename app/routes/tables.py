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

from fastapi import APIRouter, Depends, File, Form, HTTPException, Query, UploadFile
from fastapi.responses import Response
from pydantic import BaseModel, Field

from ..refine.apply import preview_or_apply, replaces_another, run_table_op
from ..refine.duplicates import duplicate_report
from ..refine.join import DEFAULT_MAX_ROWS, join_datasets, key_cardinality
from ..refine.profile import profile_columns
from ..refine.store import commit, dataset_path, in_store
from ..refine.view import page_rows
from ..security import MAX_NAME_BYTES, read_upload_capped, refuse_existing, require_key, safe_name
from ..settings import Settings, get_settings
from ..tabular import SUPPORTED_READ, read_table, write_table
from .refine import DESC_OVERWRITE, DESC_PREVIEW_TARGET, DatasetName, load_or_404, load_with_history_or_404

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
    file: UploadFile = File(description=(
        "The table: CSV, CSV.gz, JSON (an object or an array of objects), JSONL or JSONL.gz.")),
    name: str | None = Form(default=None, max_length=MAX_NAME_BYTES, description=(
        "Name to store the dataset under; default: the file name without its last extension.")),
    format: str = Form(default="auto", max_length=20, description=(
        "`auto`, or one of `csv`, `csv.gz`, `json`, `jsonl`, `jsonl.gz` to override the file name.")),
    separator: str = Form(default=";", max_length=3, description="Field separator of a CSV file."),
    encoding: str = Form(default="utf-8", max_length=20, description=(
        "Text encoding of the file, e.g. `utf-8` or `cp1252`.")),
    list_separator: str = Form(default=",", max_length=3, description=(
        "JSON only: joins a list of plain values into one cell.")),
    overwrite: bool = Form(default=False, description=(
        "Replace an existing dataset of that name, its history included; without it the name is refused "
        "with 409.")),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Import CSV or JSONL, plain or gzipped, or plain JSON.

    ``format`` defaults to ``auto``: the extension names the format and gzip is
    detected from the bytes. Declare it explicitly to override a filename that
    lies. Nested JSON arrives flattened into dot-path columns.

    Whatever comes in is stored in the one internal shape (semicolon CSV,
    UTF-8), so no later operation has to know where the data came from.

    Errors: 400 for an unsupported format, an unreadable file or encoding, a file
    without rows or columns, or a gzip inflating past ten times the upload cap; 409
    when the name is taken and `overwrite` is not set; 413 when the file exceeds the
    upload cap (DATAPREP_MAX_UPLOAD_MB).
    """
    payload = await read_upload_capped(file, settings.max_upload_mb * 1024 * 1024)
    resolved = safe_name(name or Path(file.filename or "dataset").stem, "dataset name")
    refuse_existing(dataset_path(settings, resolved).exists(), "Dataset", resolved, overwrite)
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
    # A fresh import has no steps yet; without the empty history, re-importing
    # under a used name kept the previous table's.
    await in_store(commit, settings, {resolved: (df, [])},
                   # Again at the write: the name may have been taken while this
                   # request parsed.
                   guard=lambda: refuse_existing(
                       dataset_path(settings, resolved).exists(), "Dataset", resolved, overwrite))
    return {"name": resolved, "rows": int(len(df)), "columns": list(df.columns)}


class JoinRequest(BaseModel):
    right: str = Field(max_length=MAX_NAME_BYTES, description="The dataset matched onto this one.")
    keys: list[dict] = Field(default_factory=list, description=(
        "Key column pairs `{\"left\": <column here>, \"right\": <column of right>}`; several pairs form one "
        "composite key. A row with an empty key part never matches."))
    how: str = Field(default="left", max_length=10, description="Join type: `left`, `inner`, `right` or `outer`.")
    suffix: str = Field(default="_right", max_length=20, description=(
        "Appended to a right column whose name this dataset already has."))
    coalesce: bool = Field(default=False, description=(
        "Fill empty cells of a shared column from the right instead of adding a suffixed column."))
    target: str | None = Field(default=None, max_length=MAX_NAME_BYTES, description=(
        "Name to save the joined result under; omitted = cardinality report only, nothing is written. The "
        "source's own name joins in place."))
    overwrite: bool = Field(default=False, description=DESC_OVERWRITE)


class OperationRequest(BaseModel):
    op: str = Field(max_length=40, description=(
        "Operation: `rules`, `select_columns`, `drop_columns`, `rename_columns` or `dedupe_keys`."))
    params: dict = Field(default_factory=dict, description=(
        "`rules`: {`rules`: [{`column`, `op`, `value`, `value2`, `case_sensitive`}], `combine`: `and` or "
        "`or`}; `select_columns`: {`keep`: [columns, in order]}; `drop_columns`: {`drop`: [columns]}; "
        "`rename_columns`: {`mapping`: {old: new}}; `dedupe_keys`: {`keys`: [columns], `keep`: `first` or "
        "`last`}."))
    target: str | None = Field(default=None, max_length=MAX_NAME_BYTES, description=DESC_PREVIEW_TARGET)
    overwrite: bool = Field(default=False, description=DESC_OVERWRITE)


@router.post("/{name}/op", summary="Preview (no target) or apply (target) a table operation")
async def run_operation(
    name: DatasetName, req: OperationRequest, settings: Settings = Depends(get_settings)
) -> dict:
    """Run one table operation: `rules` (keep the rows the rules match), a column
    change (`select_columns`, `drop_columns`, `rename_columns`), or `dedupe_keys`
    (one row per key; rows with an incomplete key always stay).

    Rule operators: `eq`, `ne`, `lt`, `lte`, `gt`, `gte`, `between` (with
    `value2`), `in` and `not_in` (a list), `contains`, `starts_with`, `ends_with`,
    `regex` (at most 200 characters), `is_empty`, `not_empty`. A JSON number in
    `value` compares numerically, a string as text; the four text operators ignore
    case unless `case_sensitive`.

    Without ``target`` nothing is written: a wrong filter must never cost the
    input. With one, the source's history is carried forward and this step
    appended, so a pipeline stays reconstructable.

    Errors: 400 for an unknown operation or column, or invalid parameters; 404 for
    an unknown dataset; 409 when `target` names another existing dataset and
    `overwrite` is not set.
    """
    df, history = await load_with_history_or_404(settings, name)
    target = safe_name(req.target, "target name") if req.target else None
    if target:
        refuse_existing(replaces_another(settings, name, target), "Dataset", target, req.overwrite)
    try:
        # Offload: these run over the whole frame, which would otherwise block
        # the event loop for health checks and progress polling.
        new_df, stats = await asyncio.to_thread(run_table_op, req.op, df, req.params, {})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if target is None:  # a preview writes nothing: it must not queue behind others' saves
        return preview_or_apply(settings, name, None, req.op, req.params, new_df, stats,
                                history=history)
    # The target is checked again at the write: the op ran in a thread meanwhile.
    return await in_store(preview_or_apply, settings, name, target, req.op, req.params,
                          new_df, stats, history=history, overwrite=req.overwrite)


@router.get("/{name}/profile", summary="Per-column fill rate, cardinality and ranges")
async def profile_dataset(
    name: DatasetName,
    top_n: int = Query(default=10, ge=0, le=100, description="Most frequent values listed per column; 0 = none."),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Describe an unmapped table: what is in each column, before anyone has
    decided which one is the label.

    Errors: 404 for an unknown dataset."""
    df = await load_or_404(settings, name)
    return await asyncio.to_thread(profile_columns, df, top_n=top_n)


@router.get("/{name}/download", summary="Download the dataset in a chosen format")
async def download_dataset(
    name: DatasetName,
    format: str = Query(default="csv", max_length=20, description="`csv`, `csv.gz`, `json` or `jsonl`."),
    separator: str = Query(default=";", max_length=3, description="Field separator of the CSV formats."),
    spreadsheet_safe: bool = Query(default=False, description=(
        "CSV formats only: write the variant for opening in Excel or LibreOffice: a cell that would "
        "run there as a formula gets an apostrophe in front and every field is quoted, and the file "
        "is offered as `<name>.spreadsheet.<format>`. Not for api_v3: the apostrophe becomes part of "
        "the trained text.")),
    settings: Settings = Depends(get_settings),
) -> Response:
    """Export as CSV, CSV.gz, JSON or JSONL.

    The default is the semicolon CSV api_v3 reads, so the common case is one
    click with no options.

    Errors: 400 for an unsupported format; 404 for an unknown dataset.
    """
    df = await load_or_404(settings, name)
    safe = safe_name(name, "dataset name")
    try:
        body = await asyncio.to_thread(write_table, df, fmt=format, separator=separator,
                                       spreadsheet_safe=spreadsheet_safe)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Named apart only where the flag changed something: JSON holds no formulas,
    # and a name promising a defused file for one would be a lie.
    stem = f"{safe}.spreadsheet" if spreadsheet_safe and format.startswith("csv") else safe
    return Response(
        content=body,
        media_type=_MEDIA_TYPES[format],
        # attachment, not inline: this is a file to save, and a browser would
        # otherwise render a multi-megabyte CSV as a wall of text.
        headers={"Content-Disposition": f'attachment; filename="{stem}.{format}"'},
    )


@router.get("/{name}/duplicates", summary="How many rows share a key, and which")
async def duplicates(
    name: DatasetName,
    keys: list[str] = Query(default=[], description="Key columns; repeat for a composite key"),
    examples: int = Query(default=5, ge=0, le=50, description="Repeated keys listed as examples; 0 = none."),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Non-destructive count of rows sharing a key.

    ``removable_rows`` is exactly what the ``dedupe_keys`` operation would drop,
    so the report and the removal can never disagree.

    Errors: 400 for no or an unknown key column; 404 for an unknown dataset.
    """
    df = await load_or_404(settings, name)
    try:
        return await asyncio.to_thread(duplicate_report, df, list(keys), examples=examples)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{name}/join", summary="Report a join (no target) or run it (target)")
async def join(name: DatasetName, req: JoinRequest, settings: Settings = Depends(get_settings)) -> dict:
    """Match ``right`` onto this dataset over one or more keys.

    Without a target this reports the CARDINALITY rather than a materialised
    result. That is the honest preview for a join: what an operator needs to
    know first is how many rows it would produce, and a key that repeats on both
    sides can multiply them beyond memory. Computing that from the key counts
    costs nothing, while materialising it is the very thing worth avoiding.

    Errors: 400 for a missing, malformed or unknown key, an unknown join type, or
    a result above five million rows; 404 for an unknown dataset or `right`; 409
    when `target` names another existing dataset and `overwrite` is not set.
    """
    left, history = await load_with_history_or_404(settings, name)
    right = await load_or_404(settings, req.right)
    target = safe_name(req.target, "target name") if req.target else None
    if target:
        refuse_existing(replaces_another(settings, name, target), "Dataset", target, req.overwrite)
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
    # The target is checked again at the write: the join ran in a thread meanwhile.
    return await in_store(preview_or_apply, settings, name, target, "join", params,
                          new_df, stats, history=history, overwrite=req.overwrite)


@router.get("/{name}/rows", summary="Browse rows, paginated and searchable")
async def rows(
    name: DatasetName,
    offset: int = Query(default=0, ge=0, description="Matching rows to skip (paging)."),
    limit: int = Query(default=50, ge=1, le=500, description="Page size: rows returned at most."),
    q: str = Query(default="", max_length=200, description=(
        "Case-insensitive substring searched in the shown columns; empty = no search.")),
    columns: list[str] = Query(default=[], description="Restrict to these columns"),
    settings: Settings = Depends(get_settings),
) -> dict:
    """One page of rows, with an optional case-insensitive substring search.

    Only the requested page is serialised: a 400k-row table would otherwise be
    turned into a single response. ``total`` and ``matched`` are both reported,
    because "30 of 60" is how an operator sees that a search did what they meant.

    Errors: 400 for an unknown or repeated column; 404 for an unknown dataset.
    """
    df = await load_or_404(settings, name)
    try:
        return await asyncio.to_thread(
            page_rows, df, offset=offset, limit=limit, query=q, columns=list(columns) or None
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
