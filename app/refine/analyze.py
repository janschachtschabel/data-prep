"""Refine analysis (non-destructive): distribution, duplicates, PII scan, and
the training preflight that mirrors api_v3's effective-row computation."""

from __future__ import annotations

from collections import Counter

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

    Mirrors api_v3's loader and ``prepare_targets``: clean + combine; drop the rows
    too short, without a label (container values do not count) or repeating an
    earlier row as its vectorizer sees it; ``auto_min_samples``; then keep the
    labels with ``min_samples`` rows with AND without them, dropping the rows left
    without one, until nothing changes. ``effective_rows`` matches the rows api_v3
    trains on to ±0 for an unweighted run. api_v3 repeats columns by its
    ``text_column_weights`` (title and keywords twice by default) before the length
    and duplicate checks, which this does not; on data_30k.csv that moved no row.
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
    counts = Counter(lab for labs in kept_labels for lab in set(labs))
    learnable, effective = _learnable(kept_labels, resolved_min)

    return {
        "raw_rows": int(len(df)),
        "kept_after_cleaning": n_kept,
        "dropped": dropped,
        "min_samples": resolved_min,
        "learnable_labels": len(learnable),
        "effective_rows": effective,
        "per_label_effective": {lab: counts[lab] for lab in sorted(learnable)},
        "labels_below_min": {lab: c for lab, c in counts.items() if c < resolved_min},
        # Enough rows, too few without the label: api_v3 logs and records these as ubiquitous.
        "ubiquitous_labels": {lab: c for lab, c in counts.items()
                              if c >= resolved_min and lab not in learnable},
    }


def _learnable(label_lists: list[list[str]], min_samples: int) -> tuple[set[str], int]:
    """The labels api_v3's ``prepare_targets`` trains, and how many rows keep one of them.

    A label needs ``min_samples`` rows WITH it and as many WITHOUT it: one on every row teaches
    nothing (api_v3's audit of 2026-09-30, T01). The rows left without a learnable label go, and
    that repeats until nothing changes, because those rows were negatives of the labels that
    stay. A dropped row never held a label that is still learnable, so the counts of the
    learnable labels are the ones over all kept rows.
    """
    rows = [set(labs) for labs in label_lists]
    labels: set[str] = set().union(*rows)
    while True:
        counts = Counter(lab for labs in rows for lab in labs & labels)
        keep = {lab for lab in labels
                if counts[lab] >= min_samples and len(rows) - counts[lab] >= min_samples}
        left = [labs for labs in rows if labs & keep]
        if keep == labels and len(left) == len(rows):
            return keep, len(rows)
        labels, rows = keep, left
