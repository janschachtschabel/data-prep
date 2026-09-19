"""Refine routes (part 3): bring a dataset to a minimum number of rows per label.

Its own module rather than a third operation in :mod:`app.routes.refine_prep`,
which is already at the size this project splits at -- and balancing is one
responsibility: a preview of what generation would cost, and the run that does it.
Same ``/refine`` prefix, so the URLs sit next to the other refine operations.
"""

from __future__ import annotations

import asyncio
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field, model_validator

from ..llm import BudgetExceeded, LlmConfigError, LlmError, LlmOverride, call_profile, session_for
from ..refine.apply import replaces_another
from ..refine.balance import balance_dataset, plan_balance
from ..refine.store import commit, in_store
from ..security import MAX_NAME_BYTES, llm_override, refuse_existing, require_key, safe_name
from ..settings import Settings, get_settings
from .field_spec import FieldSpec, check_fields
from .refine import (
    DEFAULT_LABEL_COLUMN,
    DESC_LABEL_SEPARATOR,
    DESC_LLM_PURPOSE,
    DESC_OVERWRITE,
    load_with_history_or_404,
)

router = APIRouter(prefix="/refine", tags=["Refine"], dependencies=[Depends(require_key)])


class BalanceRequest(BaseModel):
    """What to generate, from which fields, and where to put the result.

    ``dry_run`` returns the plan without opening an LLM session at all: this is the
    one refine operation whose cost scales with how unbalanced the data is, so the
    user sees the number of rows and batches before the first call is paid for.
    """

    fields: list[FieldSpec] = Field(min_length=1, max_length=50, description=(
        "The row's text fields, in the order a generated row fills them; the first one's values are the "
        "titles to avoid. Each column once, none a mark column (`generated_for`, `example_for`, "
        "`enriched_fields`) or the label column."))
    label_column: str = Field(default=DEFAULT_LABEL_COLUMN, max_length=200, description=(
        "Column holding each row's labels; a generated row carries the one it was made for."))
    label_separator: str = Field(default=",", min_length=1, max_length=3, description=DESC_LABEL_SEPARATOR)
    target_per_label: int = Field(ge=1, le=10_000, description=(
        "Rows every label should reach; a label below it gets the difference generated."))
    examples_per_label: int = Field(default=4, ge=1, le=20, description=(
        "Real rows of the label each prompt shows, preferring complete rows no enrichment touched and, "
        "among those, the ones closest to the label's median length."))
    batch_size: int = Field(default=10, ge=1, le=50, description="Rows requested per LLM call.")
    # cap the LLM cost per call
    limit: int = Field(default=500, ge=1, le=5000, description=(
        "Maximum rows generated in this request, shortest labels first; labels it cuts short are listed "
        "in `labels_cut_by_limit`."))
    dry_run: bool = Field(default=False, description=(
        "Return the plan and its worst-case call count only: no LLM call, nothing written, `target` unused."))
    target: str = Field(default="", max_length=MAX_NAME_BYTES, description=(
        "Name of the new dataset (source rows plus generated ones); must differ from the source. "
        "Required unless `dry_run`."))
    overwrite: bool = Field(default=False, description=DESC_OVERWRITE)
    llm_purpose: Literal["seeds", "bulk"] = Field(default="bulk", description=DESC_LLM_PURPOSE)

    @model_validator(mode="after")
    def _fields_are_usable(self) -> BalanceRequest:
        check_fields(self.fields, label_column=self.label_column)
        return self


@router.post("/{name}/balance", summary="Generate the rows each short label is missing")
async def balance(
    name: str, req: BalanceRequest,
    settings: Settings = Depends(get_settings),
    override: LlmOverride = Depends(llm_override),
) -> dict:
    """Generate the rows each label lacks to reach `target_per_label`, from that label's own real rows,
    and save source plus new rows as the new dataset `target`; the source stays unchanged. Each batch
    prompt shows real example rows, names the label by its display name (`<label_column>_DISPLAYNAME`,
    else the value), lists up to 30 other labels the new rows must not read like, and states each
    field's kind (one value, or a list with its minimum) and typical length measured on real rows,
    never above the 2,000-character answer cap; a length stated in the field's `guidance` replaces it.
    Answers are PII-scrubbed; incomplete, too short or duplicate rows are discarded, and up to two extra
    batches per label replace them. New rows are marked `generated_for=<label>`, the example rows
    `example_for`, which keeps both out of a holdout. Labels without a real row with text are skipped.
    `dry_run` returns the plan (per label: support, deficit, planned rows, batches, resulting synthetic
    share) and the worst-case call count against the call budget. Paid LLM calls; honours
    `X-LLM-Key`/`X-LLM-Model`. A budget cap mid-way keeps the rows paid for (`stopped` says why).

    Errors: 400 for an unknown column, a missing `target` or one equal to the source, or a worst case
    above the call budget; 404 for an unknown dataset; 409 when `target` exists and `overwrite` is not
    set; 429 when a budget cap stops it before the first row; 502 when an LLM call fails (nothing is
    saved); 503 when no LLM key or endpoint is configured."""
    df, history = await load_with_history_or_404(settings, name)
    fields = [spec.to_field() for spec in req.fields]
    for column in [*(f.column for f in fields), req.label_column]:
        if column not in df.columns:
            raise HTTPException(status_code=400, detail=f"Column {column!r} not found.")

    plan_args = {"fields": fields, "label_column": req.label_column,
                 "target_per_label": req.target_per_label,
                 "label_separator": req.label_separator, "batch_size": req.batch_size}

    if not req.dry_run:
        target = safe_name(req.target, "target name")
        if target.casefold() == name.casefold():
            # The store treats target == source as working in place; balancing never
            # does — the UI selects the result after a run, and a second click would
            # otherwise have grown it unpreviewed. Compared without case: on a
            # case-insensitive filesystem "Quelle" is the file "quelle".
            raise HTTPException(status_code=400, detail=(
                "Balancing writes a new dataset; choose a target name other than the source."))
        # Before any LLM call: a refused write must not have been paid for.
        refuse_existing(replaces_another(settings, name, target), "Dataset", target, req.overwrite)

    plan = await asyncio.to_thread(plan_balance, df, **plan_args, limit=req.limit)  # type: ignore[arg-type]
    per_completion, budget = call_profile(req.llm_purpose, settings, override)
    plan["max_calls"] = plan["max_batches"] * per_completion
    plan["call_budget"] = budget
    if req.dry_run:
        return plan
    if plan["max_calls"] > budget:
        # A run that hits the cap mid-way is lost whole; this one has cost nothing yet.
        raise HTTPException(status_code=400, detail=(
            f"This run could need up to {plan['max_calls']} model calls; the call budget is "
            f"{budget}. Lower the target or the limit and run it in several steps."))

    try:
        session = session_for(req.llm_purpose, settings, override)
        new_df, stats = await balance_dataset(
            df, **plan_args, complete=session.complete,  # type: ignore[arg-type]
            examples_per_label=req.examples_per_label, limit=req.limit,
            # A cap no preview can predict (tokens, the process-wide ceiling) ends
            # the run early instead of discarding what it already paid for.
            stop_on=(BudgetExceeded,),
        )
    except BudgetExceeded as exc:
        raise HTTPException(status_code=429, detail=str(exc)) from exc
    except LlmConfigError as exc:
        # Missing key: the upstream was never reached, so 503, not bad-gateway.
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except LlmError as exc:
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    if stats["stopped"] and not stats["rows_added"]:
        # Stopped before its first row: there is nothing paid for to keep.
        raise HTTPException(status_code=429, detail=stats["stopped"])

    await in_store(commit, settings, {target: (new_df, [*history, {
        "op": "balance", "source": name, "target_per_label": req.target_per_label,
        "rows_added": stats["rows_added"], "stopped": stats["stopped"],
        "usage": session.usage.as_dict()}])},
        # Again at the write: generating several hundred rows takes minutes.
        guard=lambda: refuse_existing(
            replaces_another(settings, name, target), "Dataset", target, req.overwrite))
    return {**stats, "target": target, "usage": session.usage.as_dict()}
