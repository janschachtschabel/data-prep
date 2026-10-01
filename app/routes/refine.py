"""Refine routes (part 1): the dataset store, non-destructive analysis, the
api_v3 preflight and the label-layer filters.

Part 2 -- turning a dataset into training material (split, enrich, combine,
and the round-trips to api_v3) -- is :mod:`app.routes.refine_prep`.
"""

from __future__ import annotations

import asyncio
from typing import Annotated

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from fastapi import Path as PathParam
from pydantic import BaseModel, Field

from ..config import load_config
from ..embeddings import get_encoder
from ..refine.analyze import analyze, training_preflight
from ..refine.apply import preview_or_apply, replaces_another
from ..refine.filters import run_filter
from ..refine.store import (
    delete_dataset,
    in_store,
    list_datasets,
    load_dataset,
    load_with_ops,
    stored_ops,
)
from ..security import MAX_NAME_BYTES, refuse_existing, require_key, safe_name
from ..settings import Settings, get_settings

router = APIRouter(prefix="/refine", tags=["Refine"], dependencies=[Depends(require_key)])

# The dataset name the refine routers take in their path, described once for all of
# them (refine_prep and refine_balance import it).
DatasetName = Annotated[str, PathParam(description=(
    "A dataset stored in this app, as `GET /refine/datasets` lists it. A plain name: path "
    "characters, or more than 200 bytes, are refused with 400; a name no dataset has answers 404."))]

# Test seam: tests set this to a FakeEncoder so semantic dedupe never downloads
# the real embedding model.
_test_encoder: object | None = None

# WLO defaults (verified against data_30k.csv); overridable per request.
DEFAULT_TEXT_COLUMNS = [
    "properties.cclom:title",
    "properties.cclom:general_description",
    "properties.cclom:general_keyword",
]
DEFAULT_LABEL_COLUMN = "properties.ccm:taxonid"

# Field texts the refine request models share, so one concept reads the same in every model.
DESC_LABEL_COLUMN = "Column holding each row's labels; a cell may hold several (see `label_separator`)."
DESC_LABEL_SEPARATOR = "Separator between several labels in one `label_column` cell."
DESC_OVERWRITE = ("Replace an existing dataset of that name other than the source; without it the name is "
                  "refused with 409.")
DESC_PREVIEW_TARGET = ("Name to save the result under; omitted = preview only, nothing is written. The "
                       "source's own name applies the step in place.")
DESC_LLM_PURPOSE = "LLM endpoint from config.yaml (`llm.<purpose>`) to use; `X-LLM-Model` replaces its model."


class AnalyzeRequest(BaseModel):
    text_columns: list[str] = Field(default_factory=lambda: list(DEFAULT_TEXT_COLUMNS), description=(
        "Columns whose values, cleaned as api_v3 cleans them and joined with a space, form each row's text."))
    label_column: str = Field(default=DEFAULT_LABEL_COLUMN, description=DESC_LABEL_COLUMN)
    label_separator: str = Field(default=",", max_length=3, description=DESC_LABEL_SEPARATOR)
    label_filter: str | None = Field(default=None, max_length=200, description=(
        "Optional substring: only labels containing it count, e.g. one vocabulary's URI prefix. The "
        "split ignores it."))


class PreflightRequest(AnalyzeRequest):
    min_text_length: int = Field(default=5, ge=0, le=1000, description=(
        "Rows whose cleaned text has fewer characters are dropped; set as in api_v3's training config."))
    drop_duplicates: bool = Field(default=True, description=(
        "Drop rows whose cleaned text repeats an earlier row; set as in api_v3's training config."))
    min_samples: int | None = Field(default=20, ge=1, le=10000, description=(
        "Rows a label needs, with it and as many without it, to be learnable. Omitted = 20, what "
        "api_v3's /train applies when the field is left out (its config.yaml may set another "
        "value -- send that one); null = api_v3's automatic value (2, 5, 20 or 35 for under 1k, "
        "10k, 50k or more kept rows), which /train applies to an explicit null."))


class FilterRequest(BaseModel):
    filter: str = Field(max_length=40, description=(
        "Filter to run: `dedupe_exact`, `dedupe_semantic`, `length`, `drop_no_label`, `label_filter`, "
        "`cap_per_label`, `markup` or `pii` (see the operation description)."))
    params: dict = Field(default_factory=dict, description=(
        "Filter parameters: `dedupe_semantic` {`threshold`: cosine, default 0.95}; `length` {`min`, "
        "`max`: characters of the cleaned text}; `label_filter` {`substring`}; `cap_per_label` {`cap` >= "
        "1}; `pii` {`mode`: `mask` (default) or `drop`}. The other filters take none."))
    target: str | None = Field(default=None, max_length=MAX_NAME_BYTES, description=DESC_PREVIEW_TARGET)
    overwrite: bool = Field(default=False, description=DESC_OVERWRITE)
    text_columns: list[str] = Field(default_factory=lambda: list(DEFAULT_TEXT_COLUMNS), description=(
        "Columns forming each row's text (cleaned, joined with a space) for the dedupe and length "
        "filters; `markup` and `pii` rewrite these columns."))
    label_column: str = Field(default=DEFAULT_LABEL_COLUMN, description=DESC_LABEL_COLUMN)
    label_separator: str = Field(default=",", max_length=3, description=DESC_LABEL_SEPARATOR)


def _found[T](value: T | None, name: str) -> T:
    if value is None:
        raise HTTPException(status_code=404, detail=f"Dataset {name!r} not found.")
    return value


async def load_or_404(settings: Settings, name: str) -> pd.DataFrame:
    """The stored dataset ``name`` as a frame, or 404. Shared by every route that
    reads a table; parsed on the store's thread, never on the event loop."""
    return _found(await in_store(load_dataset, settings, safe_name(name, "dataset name")), name)


async def load_with_history_or_404(settings: Settings, name: str) -> tuple[pd.DataFrame, list[dict]]:
    """The table and the history that describes it, read in one store step -- for a
    route that records its result's provenance. Or 404."""
    return _found(await in_store(load_with_ops, settings, safe_name(name, "dataset name")), name)


@router.get("/datasets", summary="List refine datasets")
async def datasets(settings: Settings = Depends(get_settings)) -> dict:
    """Every dataset in the refine store with its row count and column names. Datasets are kept as
    imported or produced: nothing is PII-scrubbed unless the `pii` filter was applied."""
    return {"datasets": await in_store(list_datasets, settings)}


@router.post("/{name}/analyze", summary="Distribution, duplicates and PII overview")
async def analyze_dataset(name: DatasetName, req: AnalyzeRequest, settings: Settings = Depends(get_settings)) -> dict:
    """Read-only overview: rows per label (and the labels under 10 rows), rows without text or label,
    exact duplicates of the cleaned text, text length (min, max, mean) and a PII scan per category.

    Errors: 400 for missing columns; 404 for an unknown dataset."""
    df = await load_or_404(settings, name)
    try:
        # Offload the whole-frame pandas work so a large dataset does not block
        # the event loop (health checks, progress polling, an in-flight run).
        return await asyncio.to_thread(
            analyze, df, req.text_columns, req.label_column,
            label_separator=req.label_separator, label_filter=req.label_filter,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{name}/preflight", summary="Simulate api_v3 preparation (effective training set)")
async def preflight(name: DatasetName, req: PreflightRequest, settings: Settings = Depends(get_settings)) -> dict:
    """Replay api_v3's data preparation to the row: clean and join the text columns as its cleaning
    version 2 does; drop rows without a label (a container value, ending in `/`, is none), too short,
    or repeating an earlier row as its vectorizer sees it (case and accents aside); then keep the labels
    with `min_samples` rows WITH them and as many without, dropping the rows left without a learnable
    label, until nothing changes. Returns the drops per reason, the resolved `min_samples`,
    `effective_rows` (what api_v3 would train on), the learnable labels with their counts, the labels
    below the minimum and the ubiquitous ones (too few rows without them). Read-only. api_v3's column
    weights are not replayed.

    Errors: 400 for missing columns; 404 for an unknown dataset."""
    df = await load_or_404(settings, name)
    try:
        return await asyncio.to_thread(
            training_preflight,
            df, req.text_columns, req.label_column,
            label_separator=req.label_separator, label_filter=req.label_filter,
            min_text_length=req.min_text_length, drop_duplicates=req.drop_duplicates,
            min_samples=req.min_samples,
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@router.post("/{name}/filter", summary="Preview (no target) or apply (target) a filter")
async def filter_dataset(name: DatasetName, req: FilterRequest, settings: Settings = Depends(get_settings)) -> dict:
    """Run one label-layer filter. Row filters: `dedupe_exact` (cleaned text seen before),
    `dedupe_semantic` (cosine similarity to a kept row at or above `threshold`, via the configured
    embedding model), `length` (cleaned text outside `min`-`max` characters), `drop_no_label`,
    `label_filter` (keep only labels containing `substring`; rows left without one go), `cap_per_label`
    (keep a row while one of its labels has fewer than `cap` kept rows; unlabelled rows go). Rewrites:
    `markup` (api_v3's text cleaning applied to the text columns), `pii` (mask e-mail, URL, phone and
    handle, or `drop` such rows). Without `target` it returns counts and examples only; with one it saves
    the result and appends the step to the source's history.

    Errors: 400 for an unknown filter, bad parameters or missing columns (the text and label columns
    must exist for every filter); 404 for an unknown dataset; 409 when `target` names another existing
    dataset and `overwrite` is not set."""
    df, history = await load_with_history_or_404(settings, name)
    target = safe_name(req.target, "target name") if req.target else None
    if target:
        refuse_existing(replaces_another(settings, name, target), "Dataset", target, req.overwrite)
    ctx: dict[str, object] = {
        "text_columns": req.text_columns,
        "label_column": req.label_column,
        "label_separator": req.label_separator,
    }
    if req.filter == "dedupe_semantic":
        cfg = load_config(settings.config_file)
        ctx["encoder"] = _test_encoder or get_encoder(cfg.embeddings.model)
    try:
        # Offload: semantic dedupe runs the embedding model + an O(N) similarity
        # loop, which would otherwise block the event loop for the whole request.
        new_df, stats = await asyncio.to_thread(run_filter, req.filter, df, req.params, ctx)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    if target is None:  # a preview writes nothing: it must not queue behind others' saves
        return preview_or_apply(settings, name, None, req.filter, req.params, new_df, stats,
                                history=history)
    # The target is checked again at the write: the filter ran in a thread meanwhile.
    return await in_store(preview_or_apply, settings, name, target, req.filter, req.params,
                          new_df, stats, history=history, overwrite=req.overwrite)


@router.get("/{name}/ops", summary="Operation history (the applied refine chain)")
async def dataset_ops(name: DatasetName, settings: Settings = Depends(get_settings)) -> dict:
    """The steps that produced the dataset, oldest first, each with its operation, parameters, source and
    counts. An import starts an empty history; combine starts a new one.

    Errors: 404 for an unknown dataset."""
    # Checked and read in one store step: apart, a delete in between was answered
    # with 200 and an empty history instead of 404.
    return {"ops": _found(await in_store(stored_ops, settings, safe_name(name, "dataset name")), name)}


@router.delete("/{name}", summary="Delete a refine dataset")
async def remove_dataset(name: DatasetName, settings: Settings = Depends(get_settings)) -> dict:
    """Delete the dataset and its history; this cannot be undone.

    Errors: 404 for an unknown dataset."""
    if not await in_store(delete_dataset, settings, safe_name(name, "dataset name")):
        raise HTTPException(status_code=404, detail=f"Dataset {name!r} not found.")
    return {"deleted": name}
