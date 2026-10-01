"""Refine analysis (non-destructive): distribution, duplicates, PII scan, and
the training preflight that mirrors api_v3's effective-row computation."""

from __future__ import annotations

import pandas as pd

from ..pii import PiiReport, scrub
from ..textnorm import clean_text, dedupe_key, is_container_label, split_labels


def auto_min_samples(n_samples: int, override: int | None = None) -> int:
    """Heuristic minimum samples per label — identical to api_v3."""
    if override is not None:
        return max(1, override)
    if n_samples < 1_000:
        return 2
    if n_samples < 10_000:
        return 5
    if n_samples < 50_000:
        return 20
    return 35


def _combined_texts(df: pd.DataFrame, text_columns: list[str]) -> list[str]:
    """Join text columns with a space after fillna('') and clean — like api_v3."""
    combined = df[text_columns[0]].fillna("")
    for col in text_columns[1:]:
        combined = combined + " " + df[col].fillna("")
    return [clean_text(t) for t in combined]


def _require_columns(df: pd.DataFrame, text_columns: list[str], label_column: str) -> None:
    missing = [c for c in (*text_columns, label_column) if c not in df.columns]
    if missing:
        raise ValueError(f"Missing columns: {', '.join(missing)}.")


def analyze(
    df: pd.DataFrame,
    text_columns: list[str],
    label_column: str,
    *,
    label_separator: str = ",",
    label_filter: str | None = None,
) -> dict:
    """Distribution, empty/missing fields, exact-duplicate and PII overview."""
    _require_columns(df, text_columns, label_column)
    texts = _combined_texts(df, text_columns)
    label_lists = [split_labels(cell, label_separator) for cell in df[label_column]]
    if label_filter:
        label_lists = [[x for x in labs if label_filter in x] for labs in label_lists]

    support: dict[str, int] = {}
    for labs in label_lists:
        for lab in set(labs):
            support[lab] = support.get(lab, 0) + 1
    lengths = [len(t) for t in texts]
    seen: set[str] = set()
    duplicates = 0
    report = PiiReport()
    for text in texts:
        if text and text in seen:
            duplicates += 1
        seen.add(text)
        report.record(scrub(text)[1] if text else {})

    ordered = sorted(support.items(), key=lambda kv: kv[1], reverse=True)
    return {
        "total_rows": int(len(df)),
        "unique_labels": len(support),
        "label_support": dict(ordered),
        "rare_labels_under_10": {k: v for k, v in support.items() if v < 10},
        "empty_text_rows": sum(1 for t in texts if not t),
        "rows_without_label": sum(1 for labs in label_lists if not labs),
        "exact_duplicate_rows": duplicates,
        "text_length": {
            "min": min(lengths) if lengths else 0,
            "max": max(lengths) if lengths else 0,
            "mean": round(sum(lengths) / len(lengths), 1) if lengths else 0.0,
        },
        "pii": report.as_dict(),
    }


def training_preflight(
    df: pd.DataFrame,
    text_columns: list[str],
    label_column: str,
    *,
    label_separator: str = ",",
    label_filter: str | None = None,
    min_text_length: int = 5,
    drop_duplicates: bool = True,
    min_samples: int | None = None,
) -> dict:
    """Simulate api_v3's data preparation and report the EFFECTIVE training set.

    Mirrors api_v3 exactly: clean + combine, min-length/no-label/duplicate row
    filter, ``auto_min_samples``, then drop rare label columns and the rows that
    lose their last label. The ``effective_rows`` figure matches api_v3's
    training log to ±0.
    """
    _require_columns(df, text_columns, label_column)
    texts = _combined_texts(df, text_columns)
    label_lists = [[lab for lab in split_labels(cell, label_separator) if not is_container_label(lab)]
                   for cell in df[label_column]]
    if label_filter:
        label_lists = [[x for x in labs if label_filter in x] for labs in label_lists]

    dropped = {"too_short": 0, "no_label": 0, "duplicate": 0}
    kept_labels: list[list[str]] = []
    seen: set[bytes] = set()
    for text, labels in zip(texts, label_lists, strict=False):
        if not labels:
            dropped["no_label"] += 1
            continue
        if len(text) < min_text_length:
            dropped["too_short"] += 1
            continue
        if drop_duplicates:
            key = dedupe_key(text)
            if key in seen:
                dropped["duplicate"] += 1
                continue
            seen.add(key)
        kept_labels.append(labels)

    n_kept = len(kept_labels)
    resolved_min = auto_min_samples(n_kept, min_samples)
    counts: dict[str, int] = {}
    for labs in kept_labels:
        for lab in set(labs):
            counts[lab] = counts.get(lab, 0) + 1
    learnable = {lab for lab, c in counts.items() if c >= resolved_min}
    effective = sum(1 for labs in kept_labels if any(lab in learnable for lab in labs))

    return {
        "raw_rows": int(len(df)),
        "kept_after_cleaning": n_kept,
        "dropped": dropped,
        "min_samples": resolved_min,
        "learnable_labels": len(learnable),
        "effective_rows": effective,
        "per_label_effective": {lab: counts[lab] for lab in sorted(learnable)},
        "labels_below_min": {lab: counts[lab] for lab, c in counts.items() if lab not in learnable},
    }
