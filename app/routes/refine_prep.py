"""Refine routes (part 2): turn a dataset into training material.

Split, enrich and combine write NEW datasets; push and label-audit are the
round-trips to api_v3. Kept apart from part 1 (:mod:`app.routes.refine`), whose
routes only inspect or filter a dataset, because these are the ones that reach
other services -- the LLM and api_v3 -- and pay for it. Same ``/refine`` prefix,
so every URL is unchanged.
"""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from ..apiv3 import PushError, predict_batch, push_csv
from ..llm import BudgetExceeded, LlmConfigError, LlmError, LlmOverride, session_for
from ..refine.analyze import _combined_texts
from ..refine.apply import replaces_another
from ..refine.combine import combine_datasets, suggest_mapping
from ..refine.enrich import enrich_dataset
from ..refine.label_audit import audit_predictions
from ..refine.prep import balance_report, holdout_split
from ..refine.store import dataset_path, read_ops, save_dataset, write_ops
from ..security import MAX_NAME_BYTES, llm_override, refuse_existing, require_key, safe_name
from ..settings import Settings, get_settings
from ..textnorm import split_labels
from .refine import DEFAULT_LABEL_COLUMN, DEFAULT_TEXT_COLUMNS, AnalyzeRequest, load_or_404

router = APIRouter(prefix="/refine", tags=["Refine"], dependencies=[Depends(require_key)])


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


@router.post("/{name}/split", summary="Stratified text-disjoint holdout split (train + holdout)")
async def split_dataset(name: str, req: SplitRequest, settings: Settings = Depends(get_settings)) -> dict:
    df = load_or_404(settings, name)
    target = safe_name(req.target, "target name")
    # Both derived names up front: checked at save time, "_train" could land
    # and "_holdout" then fail the bound, leaving half a split behind.
    # A derived name equal to the source (re-splitting "p_train" as "p") is
    # working in place, like any other step on the dataset itself.
    def check_outputs() -> None:
        for suffix in ("_train", "_holdout"):
            derived = safe_name(f"{target}{suffix}", "target name")
            refuse_existing(replaces_another(settings, name, derived), "Dataset", derived, req.overwrite)

    check_outputs()
    # Read before anything is written: unreadable, it must fail the request
    # while both outputs are still untouched.
    history = read_ops(settings, name)
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
    write_ops(settings, f"{target}_train", [*history, op])
    write_ops(settings, f"{target}_holdout", [*history, op])
    return {**stats, "target": target, "balance": balance}


@router.post("/{name}/label-audit", summary="Second opinion from api_v3 — divergence checklist")
async def label_audit(name: str, req: LabelAuditRequest, settings: Settings = Depends(get_settings)) -> dict:
    df = load_or_404(settings, name).head(req.limit)
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
    df = load_or_404(settings, name)
    target = safe_name(req.target, "target name")
    # Before any LLM call: a refused write must not have been paid for.
    refuse_existing(replaces_another(settings, name, target), "Dataset", target, req.overwrite)
    for col in (req.title_column, req.description_column, req.keyword_column):
        if col not in df.columns:
            raise HTTPException(status_code=400, detail=f"Column {col!r} not found.")
    history = read_ops(settings, name)  # before the LLM is paid for, not after
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
    write_ops(settings, target, [*history, {
        "op": "enrich", "mode": req.mode, "source": name,
        "enriched": stats["enriched"], "usage": session.usage.as_dict()}])
    return {**stats, "target": target, "usage": session.usage.as_dict()}


@router.post("/{name}/push", summary="Push a refine dataset to the configured api_v3")
async def push_dataset(name: str, settings: Settings = Depends(get_settings)) -> dict:
    df = load_or_404(settings, name)
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
        df = load_or_404(settings, name)
        result = suggest_mapping(list(df.columns), req.target_columns)
        out[name] = {**result, "columns": list(df.columns)}
    return out


@router.post("/combine", summary="Combine datasets into the target schema with conflict resolution")
async def combine(req: CombineRequest, settings: Settings = Depends(get_settings)) -> dict:
    target = safe_name(req.target, "target name")
    refuse_existing(dataset_path(settings, target).exists(), "Dataset", target, req.overwrite)
    sources = []
    for src in req.sources:
        df = load_or_404(settings, src.name)
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
