"""The text-field specification as it arrives over HTTP.

Shared by the enrichment and balancing routes. It lives apart from both so that
neither route module imports the other, and so the rule a field must satisfy is
stated once at the boundary.
"""

from __future__ import annotations

from pydantic import BaseModel, Field, model_validator

from ..refine.fields import TextField
from ..refine.provenance import MARK_COLUMNS


class FieldSpec(BaseModel):
    """One text field of the dataset — the request mirror of ``refine.fields.TextField``."""

    column: str = Field(max_length=200, description="Dataset column holding this field.")
    separator: str | None = Field(default=None, max_length=3, description=(
        "Separator between several values in one cell, for a list field such as keywords (`,`); "
        "null = one value per cell. Must not be empty."))
    min_values: int = Field(default=1, ge=1, le=50, description=(
        "Values a cell needs to be complete: fewer is a gap to fill (enrichment) or a rejected answer "
        "(balancing). Above 1 needs a `separator`."))
    guidance: str = Field(default="", max_length=1000, description=(
        "One-line instruction the prompt gives for this field. A length it states (e.g. `100-400 "
        "characters`, `3-6 keywords`, or the German equivalents) replaces the typical length measured "
        "on the dataset."))

    @model_validator(mode="after")
    def _is_a_field(self) -> FieldSpec:
        # TextField owns the rule; asking it here turns its ValueError into a 422 at
        # the boundary instead of a 500 wherever the spec is first used.
        self.to_field()
        return self

    def to_field(self) -> TextField:
        return TextField(column=self.column, separator=self.separator,
                         min_values=self.min_values, guidance=self.guidance)


def check_fields(specs: list[FieldSpec], *, label_column: str | None = None) -> None:
    """Refuse fields an engine would misuse: a column named twice would be filled
    twice, a provenance column would have its marks overwritten by generated text,
    and the label column must not be rewritten as if it were text."""
    columns = [spec.column for spec in specs]
    repeated = sorted({c for c in columns if columns.count(c) > 1})
    if repeated:
        raise ValueError(f"Field {repeated[0]!r} is named more than once.")
    reserved = [c for c in columns if c in MARK_COLUMNS]
    if reserved:
        raise ValueError(f"{reserved[0]!r} is where this app marks rows; it cannot be a text field.")
    if label_column is not None and label_column in columns:
        raise ValueError(f"The label column {label_column!r} cannot also be a text field.")
