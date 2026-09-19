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
from pydantic import BaseModel, Field, model_validator

from ..apiv3 import PushError, predict_batch, push_csv
from ..llm import BudgetExceeded, LlmConfigError, LlmError, LlmOverride, session_for
from ..refine.analyze import _combined_texts
from ..refine.apply import replaces_another
from ..refine.combine import combine_datasets, suggest_mapping
from ..refine.enrich import enrich_dataset
from ..refine.fields import TextField
from ..refine.label_audit import audit_predictions
from ..refine.prep import balance_report, holdout_split
from ..refine.store import commit, dataset_path, in_store
from ..security import MAX_NAME_BYTES, llm_override, refuse_existing, require_key, safe_name
from ..settings import Settings, get_settings
from ..textnorm import split_labels
from .field_spec import FieldSpec, check_fields
from .refine import (
    DEFAULT_LABEL_COLUMN,
    DEFAULT_TEXT_COLUMNS,
    DESC_LABEL_SEPARATOR,
    DESC_LLM_PURPOSE,
    DESC_OVERWRITE,
    AnalyzeRequest,
    load_or_404,
    load_with_history_or_404,
)

router = APIRouter(prefix="/refine", tags=["Refine"], dependencies=[Depends(require_key)])


_WLO_SCHEMA_DEFAULT = "default: the three WLO text columns and the label column."


class SuggestRequest(BaseModel):
    sources: list[str] = Field(min_length=1, max_length=20, description="Datasets to suggest a mapping for.")
    target_columns: list[str] = Field(default_factory=lambda: [*DEFAULT_TEXT_COLUMNS, DEFAULT_LABEL_COLUMN],
                                      description=f"Target columns to map; {_WLO_SCHEMA_DEFAULT}")


class CombineSource(BaseModel):
    name: str = Field(max_length=MAX_NAME_BYTES, description="A stored dataset.")
    label: str = Field(max_length=60, description="Written into the `source` column of this dataset's rows.")
    mapping: dict[str, str | None] = Field(description=(
        "Target column -> this dataset's column; null or a missing column leaves the target empty."))


class CombineRequest(BaseModel):
    sources: list[CombineSource] = Field(min_length=1, max_length=20, description=(
        "Datasets in priority order: of rows with the same text, the first source's row is kept."))
    target: str = Field(max_length=MAX_NAME_BYTES, description="Name of the new dataset.")
    overwrite: bool = Field(default=False, description=(
        "Replace an existing dataset of that name, a source included; without it the name is refused with 409."))
    target_columns: list[str] = Field(default_factory=lambda: [*DEFAULT_TEXT_COLUMNS, DEFAULT_LABEL_COLUMN],
                                      description=f"Columns of the result, in order; {_WLO_SCHEMA_DEFAULT}")
    text_columns: list[str] = Field(default_factory=lambda: list(DEFAULT_TEXT_COLUMNS), description=(
        "Target columns whose cleaned, joined values identify a duplicate; must be within `target_columns`."))


class SplitRequest(AnalyzeRequest):
    holdout_fraction: float = Field(default=0.15, gt=0.0, lt=0.9, description=(
        "Share of each label's real (not generated) rows to hold out, at least one row; whole text groups move."))
    seed: int = Field(default=42, ge=0, description="Random seed: same seed and data, same split.")
    target: str = Field(max_length=MAX_NAME_BYTES, description=(
        "Base name of the two outputs `<target>_train` and `<target>_holdout`."))
    overwrite: bool = Field(default=False, description=(
        "Replace existing datasets of those names other than the source; without it such a name is refused "
        "with 409."))


class LabelAuditRequest(AnalyzeRequest):
    model_name: str = Field(max_length=100, description="Trained api_v3 model to ask.")
    confidence_threshold: float = Field(default=0.8, ge=0.0, le=1.0, description=(
        "Flag a row only when the top prediction's confidence is at least this."))
    top_k: int = Field(default=3, ge=1, le=20, description=(
        "Predictions per row; a row is flagged only if none of its labels is among them."))
    # cap the api_v3 predict load
    limit: int = Field(default=2000, ge=1, le=20000, description="Audit only the first `limit` rows.")


# What the two shipped prompts asked for, kept verbatim as the guidance of the WLO
# fields: a request in the old shape keeps the instructions it always carried -- their
# numbers included, which is why its prompt adds no length from the dataset beside them
# (prompt_context.guidance_states_a_length). The UI's defaults for the same fields name
# no number (static/ui/refine-fields.js): there the dataset says how long.
_WLO_KEYWORD_GUIDANCE = (
    "Nenne 3-6 treffende deutsche Schlagwörter (kommagetrennt), die den Inhalt "
    "erschließen."
)
_WLO_DESCRIPTION_GUIDANCE = (
    "Schreibe eine sachliche Beschreibung (100-400 Zeichen), was dieses Material bietet."
)


class EnrichRequest(BaseModel):
    """Either shape: ``fields`` + ``target_field``, or the original ``mode`` with the
    three WLO columns. The original keeps working with the instructions it always
    carried, numbers included; either way the prompt names the row's labels."""

    target: str = Field(max_length=MAX_NAME_BYTES, description=(
        "Name to save the result under; the source's own name enriches in place."))
    overwrite: bool = Field(default=False, description=DESC_OVERWRITE)
    # cap the LLM cost per call
    limit: int = Field(default=500, ge=1, le=5000, description=(
        "Maximum LLM calls: one per row with a gap, in row order; later gaps stay open, except a row "
        "whose fields all match a row answered before -- it takes that answer, without a call."))
    llm_purpose: Literal["seeds", "bulk"] = Field(default="bulk", description=DESC_LLM_PURPOSE)
    # Refused as a field -- model text in a label cell is an invented label -- and named
    # in each prompt, so the added text fits the row's classification.
    label_column: str = Field(default=DEFAULT_LABEL_COLUMN, max_length=200, description=(
        "Column holding each row's labels, which each prompt names (as `<label_column>_DISPLAYNAME` "
        "where present); cannot be a field, ignored when the dataset lacks it."))
    label_separator: str = Field(default=",", min_length=1, max_length=3, description=DESC_LABEL_SEPARATOR)

    fields: list[FieldSpec] | None = Field(default=None, max_length=50, description=(
        "The row's text fields; the others' values are the prompt's context. Each column once, none a "
        "mark column (`generated_for`, `example_for`, `enriched_fields`) or the label column."))
    target_field: str | None = Field(default=None, max_length=200, description="Column of `fields` to fill.")

    mode: Literal["keywords", "description"] | None = Field(default=None, description=(
        "Original shape, used without `fields` + `target_field`: fill the keywords or the description."))
    title_column: str = Field(default="properties.cclom:title", description="Original shape: title column.")
    description_column: str = Field(default="properties.cclom:general_description",
                                    description="Original shape: description column.")
    keyword_column: str = Field(default="properties.cclom:general_keyword",
                                description="Original shape: comma-separated keyword column.")
    min_keywords: int = Field(default=3, ge=1, le=20, description="Original shape: fewer keywords is a gap.")

    @model_validator(mode="after")
    def _fields_are_usable(self) -> EnrichRequest:
        if self.fields:
            check_fields(self.fields, label_column=self.label_column)
        return self

    def resolve(self) -> tuple[list[TextField], str]:
        """The fields to work on and the one to fill, from whichever shape arrived."""
        if self.fields and self.target_field:
            if self.target_field not in {spec.column for spec in self.fields}:
                raise ValueError(
                    f"target_field {self.target_field!r} is not among the fields."
                )
            return [spec.to_field() for spec in self.fields], _markable(self.target_field)
        if self.mode is None:
            raise ValueError(
                "Send either 'fields' with 'target_field', or 'mode' with the column names."
            )
        fields = [
            TextField(column=self.title_column),
            TextField(column=self.description_column, guidance=_WLO_DESCRIPTION_GUIDANCE),
            TextField(column=self.keyword_column, separator=",",
                      min_values=self.min_keywords, guidance=_WLO_KEYWORD_GUIDANCE),
        ]
        target = self.keyword_column if self.mode == "keywords" else self.description_column
        return fields, _markable(target)


def _markable(column: str) -> str:
    """``column``, when ``enriched_fields`` can name it: that cell joins the columns it
    names with "," and its readers strip each, so a comma or surrounding spaces would
    read back as another column -- and the row lose its mark wherever it is checked per
    column."""
    if "," in column or column != column.strip():
        raise ValueError(
            f"Column {column!r} cannot be enriched: enriched_fields names columns joined by ',', "
            "so the name must hold no comma and no leading or trailing spaces.")
    return column


@router.post("/{name}/split", summary="Stratified text-disjoint holdout split (train + holdout)")
async def split_dataset(name: str, req: SplitRequest, settings: Settings = Depends(get_settings)) -> dict:
    """Write `<target>_train` and `<target>_holdout`: text-disjoint (rows with the same cleaned text stay on
    one side) and stratified (text groups join the holdout in seeded order until each label has
    `holdout_fraction` of its real rows). Rows with an AI mark (`generated_for`, `example_for`,
    `enriched_fields`), and every row sharing its text with one, stay in training. Returns the counts,
    `labels_without_holdout` (labels this left without a holdout row; split before balancing to avoid
    them) and a label report of the training part.

    Errors: 400 for missing columns; 404 for an unknown dataset; 409 when an output name is taken and
    `overwrite` is not set."""
    # The history is read with the table, before anything is written: unreadable,
    # it must fail the request while both outputs are still untouched.
    df, history = await load_with_history_or_404(settings, name)
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
    op = {"op": "holdout_split", "source": name, "holdout_fraction": req.holdout_fraction,
          "seed": req.seed, **stats}
    # The source's steps plus this one, REPLACING whatever the target names
    # held: appended, a reused name kept the history of a different table.
    # Both names are checked again at the write: the split ran in a thread meanwhile.
    await in_store(commit, settings, {
        f"{target}_train": (train, [*history, op]),
        f"{target}_holdout": (holdout, [*history, op]),
    }, guard=check_outputs)
    return {**stats, "target": target, "balance": balance}


@router.post("/{name}/label-audit", summary="Second opinion from api_v3 — divergence checklist")
async def label_audit(name: str, req: LabelAuditRequest, settings: Settings = Depends(get_settings)) -> dict:
    """Ask a trained api_v3 model (`/predict/batch`) about the first `limit` rows and flag each row whose
    top prediction reaches `confidence_threshold` while none of its labels is in the top `top_k`: a
    checklist for editors. Nothing is relabelled or written.

    Errors: 400 for missing columns, or when api_v3 is not configured, not allowed or has no key; 404 for
    an unknown dataset; 502 when api_v3 refuses or cannot be reached."""
    df = (await load_or_404(settings, name)).head(req.limit)
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
    """Fill the gaps of one field with the LLM, additively: a cell with fewer than `min_values` values is a
    gap, and a short list is extended, never replaced. Each prompt shows the row's other fields and names
    its labels (from `label_column`, by display name where known) and the field's typical length in the
    rows people wrote, at most 1,500 characters (an answer value over 2,000 is dropped); a length stated in the field's
    `guidance` replaces it. Answers are PII-scrubbed; changed rows are marked in `enriched_fields`, which
    keeps them out of a holdout. Saves the result as `target`. Paid LLM calls; honours
    `X-LLM-Key`/`X-LLM-Model`. A budget cap mid-way keeps what was paid for (`stopped` says why).

    Errors: 400 for an unknown column or an incomplete request shape; 404 for an unknown dataset; 409
    when `target` names another existing dataset and `overwrite` is not set; 429 when a budget cap stops
    it before the first change; 502 when an LLM call fails (nothing is saved); 503 when no LLM key or
    endpoint is configured."""
    df, history = await load_with_history_or_404(settings, name)  # before the LLM is paid for
    target = safe_name(req.target, "target name")
    # Before any LLM call: a refused write must not have been paid for.
    refuse_existing(replaces_another(settings, name, target), "Dataset", target, req.overwrite)
    try:
        fields, target_field = req.resolve()
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    for col in [f.column for f in fields]:
        if col not in df.columns:
            raise HTTPException(status_code=400, detail=f"Column {col!r} not found.")
    try:
        session = session_for(req.llm_purpose, settings, override)
        new_df, stats = await enrich_dataset(
            df, fields=fields, target_field=target_field,
            complete=session.complete, limit=req.limit,
            label_column=req.label_column, label_separator=req.label_separator,
            # A cap no request can predict (tokens, the process-wide ceiling) ends
            # the run early instead of discarding what it already paid for.
            stop_on=(BudgetExceeded,),
        )
    except BudgetExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except LlmConfigError as exc:
        # Missing key: the upstream was never reached, so 503 (like the seed
        # routes), not bad-gateway.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LlmError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc
    if stats["stopped"] and not stats["enriched"]:
        # Stopped before its first change: there is nothing to keep.
        raise HTTPException(status_code=429, detail=stats["stopped"])
    await in_store(commit, settings, {target: (new_df, [*history, {
        "op": "enrich", "field": target_field, "source": name,
        "enriched": stats["enriched"], "stopped": stats["stopped"],
        "usage": session.usage.as_dict()}])},
        # Again at the write: the LLM calls can take minutes.
        guard=lambda: refuse_existing(
            replaces_another(settings, name, target), "Dataset", target, req.overwrite))
    return {**stats, "target": target, "usage": session.usage.as_dict()}


@router.post("/{name}/push", summary="Push a refine dataset to the configured api_v3")
async def push_dataset(name: str, settings: Settings = Depends(get_settings)) -> dict:
    """Upload the whole dataset as `<name>.csv` (semicolon CSV) to `/datasets/import` of the api_v3
    configured in config.yaml (`api_v3.url`), with the key from the env variable it names.

    Errors: 400 when api_v3 is not configured, not allowed or has no key; 404 for an unknown dataset; 409
    when api_v3 already has a dataset of that name; 502 when api_v3 refuses or cannot be reached."""
    df = await load_or_404(settings, name)
    # As costly as saving it, so off the loop like a save.
    csv_text = await asyncio.to_thread(lambda: df.to_csv(sep=";", index=False))
    try:
        body = await push_csv(settings, csv_text, f"{safe_name(name, 'dataset name')}.csv")
    except PushError as exc:
        raise HTTPException(status_code=exc.status, detail=exc.detail) from exc
    return {"pushed": f"{name}.csv", "rows": int(len(df)), "api_v3": body}


@router.post("/combine/suggest", summary="Suggest column mappings for source datasets")
async def combine_suggest(req: SuggestRequest, settings: Settings = Depends(get_settings)) -> dict:
    """Per source: a best-effort mapping of each target column to one of its columns (same name, then same
    last word as in `general_description` ~ `description`, then substring), the unmatched targets, the
    unused columns and all its columns. Read-only.

    Errors: 404 for an unknown dataset."""
    out: dict[str, dict] = {}
    for name in req.sources:
        df = await load_or_404(settings, name)
        result = suggest_mapping(list(df.columns), req.target_columns)
        out[name] = {**result, "columns": list(df.columns)}
    return out


@router.post("/combine", summary="Combine datasets into the target schema with conflict resolution")
async def combine(req: CombineRequest, settings: Settings = Depends(get_settings)) -> dict:
    """Map every source onto `target_columns` and concatenate them into the new dataset `target`, with a
    `source` column naming each row's source. Of rows with the same cleaned text (`text_columns`) only
    the first, highest-priority one is kept. AI marks (`generated_for`, `example_for`, `enriched_fields`)
    are carried along unmapped, so marked rows stay out of later holdouts. The history starts anew.

    Errors: 400 when `text_columns` is empty or not within `target_columns`; 404 for an unknown source;
    409 when `target` exists and `overwrite` is not set."""
    target = safe_name(req.target, "target name")
    refuse_existing(dataset_path(settings, target).exists(), "Dataset", target, req.overwrite)
    sources = []
    for src in req.sources:
        df = await load_or_404(settings, src.name)
        sources.append({"df": df, "label": src.label, "mapping": src.mapping})
    try:
        combined, stats = await asyncio.to_thread(
            combine_datasets, sources, req.target_columns, text_columns=req.text_columns
        )
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc
    # A new table made from several: no single source's history describes it,
    # so the history starts with this step (the op names the sources).
    await in_store(commit, settings, {target: (combined, [{
        "op": "combine", "sources": [s.name for s in req.sources],
        "total_in": stats["total_in"], "total_out": stats["total_out"],
        "conflicts_resolved": stats["conflicts_resolved"]}])},
        # Again at the write: the combination ran in a thread meanwhile.
        guard=lambda: refuse_existing(
            dataset_path(settings, target).exists(), "Dataset", target, req.overwrite))
    return {**stats, "target": target}
