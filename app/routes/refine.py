"""Refine routes (part 1): the dataset store, non-destructive analysis, the
api_v3 preflight and the label-layer filters.

Part 2 -- turning a dataset into training material (split, enrich, combine,
and the round-trips to api_v3) -- is :mod:`app.routes.refine_prep`.
"""

from __future__ import annotations

import asyncio

import pandas as pd
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..config import load_config
from ..embeddings import get_encoder
from ..refine.analyze import analyze, training_preflight
from ..refine.apply import preview_or_apply, replaces_another
from ..refine.filters import run_filter
from ..refine.store import (
    dataset_path,
    delete_dataset,
    in_store,
    list_datasets,
    load_dataset,
    read_ops,
)
from ..security import MAX_NAME_BYTES, refuse_existing, require_key, safe_name
from ..settings import Settings, get_settings

router = APIRouter(prefix="/refine", tags=["Refine"], dependencies=[Depends(require_key)])

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


class AnalyzeRequest(BaseModel):
    text_columns: list[str] = Field(default_factory=lambda: list(DEFAULT_TEXT_COLUMNS))
    label_column: str = DEFAULT_LABEL_COLUMN
    label_separator: str = Field(default=",", max_length=3)
    label_filter: str | None = Field(default=None, max_length=200)


class PreflightRequest(AnalyzeRequest):
    min_text_length: int = Field(default=5, ge=0, le=1000)
    drop_duplicates: bool = True
    min_samples: int | None = Field(default=None, ge=1, le=10000)


class FilterRequest(BaseModel):
    filter: str = Field(max_length=40)
    params: dict = Field(default_factory=dict)
    target: str | None = Field(default=None, max_length=MAX_NAME_BYTES)  # None = preview only
    overwrite: bool = False
    text_columns: list[str] = Field(default_factory=lambda: list(DEFAULT_TEXT_COLUMNS))
    label_column: str = DEFAULT_LABEL_COLUMN
    label_separator: str = Field(default=",", max_length=3)


async def load_or_404(settings: Settings, name: str) -> pd.DataFrame:
    """The stored dataset ``name`` as a frame, or 404. Shared by every route that
    reads a table; parsed on the store's thread, never on the event loop."""
    df = await in_store(load_dataset, settings, safe_name(name, "dataset name"))
    if df is None:
        raise HTTPException(status_code=404, detail=f"Dataset {name!r} not found.")
    return df


@router.get("/datasets", summary="List refine datasets")
async def datasets(settings: Settings = Depends(get_settings)) -> dict:
    return {"datasets": await in_store(list_datasets, settings)}


@router.post("/{name}/analyze", summary="Distribution, duplicates and PII overview")
async def analyze_dataset(name: str, req: AnalyzeRequest, settings: Settings = Depends(get_settings)) -> dict:
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
async def preflight(name: str, req: PreflightRequest, settings: Settings = Depends(get_settings)) -> dict:
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
async def filter_dataset(name: str, req: FilterRequest, settings: Settings = Depends(get_settings)) -> dict:
    df = await load_or_404(settings, name)
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
    # The target is checked again at the write: the filter ran in a thread meanwhile.
    return await in_store(preview_or_apply, settings, name, target, req.filter, req.params,
                          new_df, stats, overwrite=req.overwrite)


@router.get("/{name}/ops", summary="Operation history (the applied refine chain)")
async def dataset_ops(name: str, settings: Settings = Depends(get_settings)) -> dict:
    safe = safe_name(name, "dataset name")
    if not dataset_path(settings, safe).exists():
        raise HTTPException(status_code=404, detail=f"Dataset {name!r} not found.")
    return {"ops": await in_store(read_ops, settings, safe)}


@router.delete("/{name}", summary="Delete a refine dataset")
async def remove_dataset(name: str, settings: Settings = Depends(get_settings)) -> dict:
    if not await in_store(delete_dataset, settings, safe_name(name, "dataset name")):
        raise HTTPException(status_code=404, detail=f"Dataset {name!r} not found.")
    return {"deleted": name}
