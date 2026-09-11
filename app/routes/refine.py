"""Refine routes (part 1): dataset store + non-destructive analysis + preflight."""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..apiv3 import PushError, predict_batch, push_csv
from ..config import load_config
from ..embeddings import get_encoder
from ..llm import BudgetExceeded, LlmConfigError, LlmError, LlmOverride, session_for
from ..refine.analyze import _combined_texts, analyze, training_preflight
from ..refine.apply import preview_or_apply, replaces_another
from ..refine.combine import combine_datasets, suggest_mapping
from ..refine.enrich import enrich_dataset
from ..refine.filters import run_filter
from ..refine.label_audit import audit_predictions
from ..refine.prep import balance_report, holdout_split
from ..refine.store import (
    dataset_path,
    delete_dataset,
    list_datasets,
    load_dataset,
    read_ops,
    save_dataset,
    write_ops,
)
from ..security import MAX_NAME_BYTES, llm_override, refuse_existing, require_key, safe_name
from ..settings import Settings, get_settings
from ..textnorm import split_labels

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


class SuggestRequest(BaseModel):
    sources: list[str] = Field(min_length=1, max_length=20)
    target_columns: list[str] = Field(default_factory=lambda: [*DEFAULT_TEXT_COLUMNS, DEFAULT_LABEL_COLUMN])


class CombineSource(BaseModel):
    name: str = Field(max_length=MAX_NAME_BYTES)
    label: str = Field(max_length=60)
    mapping: dict[str, str | None]


class CombineRequest(BaseModel):
    sources: list[CombineSource] = Field(min_length=1, max_length=20)
    target: str = Field(max_length=MAX_NAME_BYTES)
    overwrite: bool = False
    target_columns: list[str] = Field(default_factory=lambda: [*DEFAULT_TEXT_COLUMNS, DEFAULT_LABEL_COLUMN])
    text_columns: list[str] = Field(default_factory=lambda: list(DEFAULT_TEXT_COLUMNS))


class SplitRequest(AnalyzeRequest):
    holdout_fraction: float = Field(default=0.15, gt=0.0, lt=0.9)
    seed: int = Field(default=42, ge=0)
    target: str = Field(max_length=MAX_NAME_BYTES)
    overwrite: bool = False


class LabelAuditRequest(AnalyzeRequest):
    model_name: str = Field(max_length=100)
    confidence_threshold: float = Field(default=0.8, ge=0.0, le=1.0)
    top_k: int = Field(default=3, ge=1, le=20)
    limit: int = Field(default=2000, ge=1, le=20000)  # cap the api_v3 predict load


class EnrichRequest(BaseModel):
    mode: Literal["keywords", "description"]
    target: str = Field(max_length=MAX_NAME_BYTES)
    overwrite: bool = False
    title_column: str = "properties.cclom:title"
    description_column: str = "properties.cclom:general_description"
    keyword_column: str = "properties.cclom:general_keyword"
    min_keywords: int = Field(default=3, ge=1, le=20)
    limit: int = Field(default=500, ge=1, le=5000)  # cap the LLM cost per call
    llm_purpose: Literal["seeds", "bulk"] = "bulk"


def _load_or_404(settings: Settings, name: str):
    df = load_dataset(settings, safe_name(name, "dataset name"))
    if df is None:
        raise HTTPException(status_code=404, detail=f"Dataset {name!r} not found.")
    return df


@router.get("/datasets", summary="List refine datasets")
async def datasets(settings: Settings = Depends(get_settings)) -> dict:
    return {"datasets": list_datasets(settings)}


@router.post("/{name}/analyze", summary="Distribution, duplicates and PII overview")
async def analyze_dataset(name: str, req: AnalyzeRequest, settings: Settings = Depends(get_settings)) -> dict:
    df = _load_or_404(settings, name)
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
    df = _load_or_404(settings, name)
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
    df = _load_or_404(settings, name)
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
    if target:  # again at the write: the filter ran in a thread meanwhile
        refuse_existing(replaces_another(settings, name, target), "Dataset", target, req.overwrite)
    return preview_or_apply(settings, name, target, req.filter, req.params, new_df, stats)


@router.post("/{name}/split", summary="Stratified text-disjoint holdout split (train + holdout)")
async def split_dataset(name: str, req: SplitRequest, settings: Settings = Depends(get_settings)) -> dict:
    df = _load_or_404(settings, name)
    target = safe_name(req.target, "target name")
    # Both derived names up front: checked at save time, "_train" could land
    # and "_holdout" then fail the bound, leaving half a split behind.
    def check_outputs() -> None:
        for suffix in ("_train", "_holdout"):
            derived = safe_name(f"{target}{suffix}", "target name")
            refuse_existing(dataset_path(settings, derived).exists(), "Dataset", derived, req.overwrite)

    check_outputs()
    try:
        train, holdout, stats = await asyncio.to_thread(
            holdout_split, df, req.text_columns, req.label_column,
            holdout_fraction=req.holdout_fraction, seed=req.seed,
            label_separator=req.label_separator,
        )
        balance = await asyncio.to_thread(
            balance_report, train, req.text_columns, req.label_column,
            label_separator=req.label_separator,
        )
    except (ValueError, KeyError) as exc:
        raise HTTPException(status_code=400, detail=f"Split failed: {exc}") from exc
    check_outputs()  # again at the write: the split ran in a thread meanwhile
    save_dataset(settings, f"{target}_train", train)
    save_dataset(settings, f"{target}_holdout", holdout)
    op = {"op": "holdout_split", "source": name, "holdout_fraction": req.holdout_fraction,
          "seed": req.seed, **stats}
    # The source's steps plus this one, REPLACING whatever the target names
    # held: appended, a reused name kept the history of a different table.
    history = [*read_ops(settings, name), op]
    write_ops(settings, f"{target}_train", history)
    write_ops(settings, f"{target}_holdout", history)
    return {**stats, "target": target, "balance": balance}


@router.post("/{name}/label-audit", summary="Second opinion from api_v3 — divergence checklist")
async def label_audit(name: str, req: LabelAuditRequest, settings: Settings = Depends(get_settings)) -> dict:
    df = _load_or_404(settings, name).head(req.limit)
    missing = [c for c in (*req.text_columns, req.label_column) if c not in df.columns]
    if missing:
        raise HTTPException(status_code=400, detail=f"Missing columns: {', '.join(missing)}.")
    texts = _combined_texts(df, req.text_columns)
    gold = [
        {lab for lab in split_labels(cell, req.label_separator)
         if not req.label_filter or req.label_filter in lab}
        for cell in df[req.label_column]
    ]
    try:
        predictions = await predict_batch(settings, texts, req.model_name, top_k=req.top_k)
    except PushError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    flagged = audit_predictions(gold, predictions,
                                confidence_threshold=req.confidence_threshold, top_k=req.top_k)
    return {"rows_audited": int(len(df)), "flagged_count": len(flagged), "checklist": flagged}


@router.post("/{name}/enrich", summary="Additive LLM completion of missing fields (marked)")
async def enrich(
    name: str, req: EnrichRequest,
    settings: Settings = Depends(get_settings),
    override: LlmOverride = Depends(llm_override),
) -> dict:
    df = _load_or_404(settings, name)
    target = safe_name(req.target, "target name")
    # Before any LLM call: a refused write must not have been paid for.
    refuse_existing(replaces_another(settings, name, target), "Dataset", target, req.overwrite)
    for col in (req.title_column, req.description_column, req.keyword_column):
        if col not in df.columns:
            raise HTTPException(status_code=400, detail=f"Column {col!r} not found.")
    session = session_for(req.llm_purpose, settings, override)
    try:
        new_df, stats = await enrich_dataset(
            df, title_col=req.title_column, description_col=req.description_column,
            keyword_col=req.keyword_column, mode=req.mode, min_keywords=req.min_keywords,
            complete=session.complete, limit=req.limit,
        )
    except BudgetExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except LlmConfigError as exc:
        # Missing key: the upstream was never reached, so 503 (like the seed
        # routes), not bad-gateway.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LlmError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    # Again at the write: the LLM calls can take minutes.
    refuse_existing(replaces_another(settings, name, target), "Dataset", target, req.overwrite)
    save_dataset(settings, target, new_df)
    write_ops(settings, target, [*read_ops(settings, name), {
        "op": "enrich", "mode": req.mode, "source": name,
        "enriched": stats["enriched"], "usage": session.usage.as_dict()}])
    return {**stats, "target": target, "usage": session.usage.as_dict()}


@router.post("/{name}/push", summary="Push a refine dataset to the configured api_v3")
async def push_dataset(name: str, settings: Settings = Depends(get_settings)) -> dict:
    df = _load_or_404(settings, name)
    csv_text = df.to_csv(sep=";", index=False)
    try:
        body = await push_csv(settings, csv_text, f"{safe_name(name, 'dataset name')}.csv")
    except PushError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    return {"pushed": f"{name}.csv", "rows": int(len(df)), "api_v3": body}


@router.post("/combine/suggest", summary="Suggest column mappings for source datasets")
async def combine_suggest(req: SuggestRequest, settings: Settings = Depends(get_settings)) -> dict:
    out: dict[str, dict] = {}
    for name in req.sources:
        df = _load_or_404(settings, name)
        result = suggest_mapping(list(df.columns), req.target_columns)
        out[name] = {**result, "columns": list(df.columns)}
    return out


@router.post("/combine", summary="Combine datasets into the target schema with conflict resolution")
async def combine(req: CombineRequest, settings: Settings = Depends(get_settings)) -> dict:
    target = safe_name(req.target, "target name")
    refuse_existing(dataset_path(settings, target).exists(), "Dataset", target, req.overwrite)
    sources = []
    for src in req.sources:
        df = _load_or_404(settings, src.name)
        sources.append({"df": df, "label": src.label, "mapping": src.mapping})
    try:
        combined, stats = await asyncio.to_thread(
            combine_datasets, sources, req.target_columns, text_columns=req.text_columns
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # Again at the write: the combination ran in a thread meanwhile.
    refuse_existing(dataset_path(settings, target).exists(), "Dataset", target, req.overwrite)
    save_dataset(settings, target, combined)
    # A new table made from several: no single source's history describes it,
    # so the history starts with this step (the op names the sources).
    write_ops(settings, target, [{"op": "combine", "sources": [s.name for s in req.sources],
                                  "total_in": stats["total_in"], "total_out": stats["total_out"],
                                  "conflicts_resolved": stats["conflicts_resolved"]}])
    return {**stats, "target": target}


@router.get("/{name}/ops", summary="Operation history (the applied refine chain)")
async def dataset_ops(name: str, settings: Settings = Depends(get_settings)) -> dict:
    safe = safe_name(name, "dataset name")
    if not dataset_path(settings, safe).exists():
        raise HTTPException(status_code=404, detail=f"Dataset {name!r} not found.")
    return {"ops": read_ops(settings, safe)}


@router.delete("/{name}", summary="Delete a refine dataset")
async def remove_dataset(name: str, settings: Settings = Depends(get_settings)) -> dict:
    if not delete_dataset(settings, safe_name(name, "dataset name")):
        raise HTTPException(status_code=404, detail=f"Dataset {name!r} not found.")
    return {"deleted": name}
