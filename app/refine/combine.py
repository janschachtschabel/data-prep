"""Refine combine: map arbitrary source columns to the api_v3 target schema and
merge multiple datasets, tagging provenance and resolving text-duplicate
conflicts by source priority (first source wins)."""

from __future__ import annotations

import re

import pandas as pd

from .analyze import _combined_texts

_TOKEN_RE = re.compile(r"[a-z0-9]+")


def _leaf(column: str) -> str:
    """Last alphanumeric token of a column name, lowercased — so
    ``properties.cclom:general_description`` and ``general_description`` both
    reduce to ``description``."""
    tokens = _TOKEN_RE.findall(column.lower())
    return tokens[-1] if tokens else column.lower()


def suggest_mapping(source_columns: list[str], target_columns: list[str]) -> dict:
    """Best-effort target→source column mapping (exact, then leaf-equality, then
    substring). Unmatched targets map to ``None`` for the user to fix in the UI."""
    remaining = list(source_columns)
    mapping: dict[str, str | None] = {}
    for target in target_columns:
        target_leaf = _leaf(target)
        match = (
            next((c for c in remaining if c == target), None)
            or next((c for c in remaining if _leaf(c) == target_leaf), None)
            or next((c for c in remaining if target_leaf in c.lower()), None)
        )
        mapping[target] = match
        if match is not None:
            remaining.remove(match)
    return {
        "mapping": mapping,
        "unmatched_targets": [t for t, s in mapping.items() if s is None],
        "unused_sources": remaining,
    }


def combine_datasets(
    sources: list[dict],
    target_columns: list[str],
    *,
    text_columns: list[str],
    label_separator: str = ",",  # noqa: ARG001 - reserved for future label merging
) -> tuple[pd.DataFrame, dict]:
    """Merge ``sources`` (priority order, first wins) into the target schema.

    Each source: ``{"df", "mapping": {target: source_col}, "label"}``. A
    ``source`` column records provenance; rows with a duplicate combined text
    keep the highest-priority occurrence.
    """
    # text_columns build the dedupe key off the combined (target-shaped) frame.
    # Empty -> text_columns[0] IndexErrors; one outside target_columns -> KeyError.
    # Both would surface as HTTP 500, so reject them up front (route maps to 400).
    if not text_columns:
        raise ValueError("text_columns must not be empty.")
    missing = [c for c in text_columns if c not in target_columns]
    if missing:
        raise ValueError(f"text_columns must be within target_columns; not in target: {', '.join(missing)}")
    frames: list[pd.DataFrame] = []
    per_source: list[dict] = []
    for src in sources:
        df, mapping, label = src["df"], src["mapping"], src["label"]
        out = pd.DataFrame(index=df.index)
        for target in target_columns:
            source_col = mapping.get(target)
            out[target] = df[source_col].fillna("") if source_col and source_col in df.columns else ""
        out["source"] = label
        frames.append(out)
        per_source.append({"label": label, "rows_in": int(len(df))})

    combined = pd.concat(frames, ignore_index=True) if frames else pd.DataFrame(columns=[*target_columns, "source"])
    if not combined.empty:
        key = pd.Series(_combined_texts(combined, text_columns), index=combined.index)
        combined = combined[~key.duplicated(keep="first")].reset_index(drop=True)

    kept = combined["source"].value_counts().to_dict() if not combined.empty else {}
    for entry in per_source:
        entry["rows_kept"] = int(kept.get(entry["label"], 0))
    total_in = sum(e["rows_in"] for e in per_source)
    stats = {
        "total_in": total_in,
        "total_out": int(len(combined)),
        "conflicts_resolved": total_in - int(len(combined)),
        "per_source": per_source,
    }
    return combined, stats
