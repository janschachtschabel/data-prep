"""Refine training preparation: a stratified, text-disjoint holdout split and a
balance report.

The split generalizes the honest-evaluation methodology from api_v3's
``enrich_tail.py``: rows sharing a combined text always land on the SAME side
(text-disjoint, so a holdout text never leaked into training), and each label is
pulled into holdout until it reaches its target fraction (stratification), so
every sufficiently-supported label is represented on both sides.
"""

from __future__ import annotations

import random
from collections import defaultdict

import pandas as pd

from ..textnorm import split_labels
from .analyze import _combined_texts, auto_min_samples


def holdout_split(
    df: pd.DataFrame,
    text_columns: list[str],
    label_column: str,
    *,
    holdout_fraction: float = 0.15,
    seed: int = 42,
    label_separator: str = ",",
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Split into (train, holdout, stats), text-disjoint and label-stratified."""
    texts = _combined_texts(df, text_columns)
    labels = [split_labels(cell, label_separator) for cell in df[label_column]]

    groups: dict[str, list[int]] = defaultdict(list)
    for i, text in enumerate(texts):
        groups[text].append(i)

    support: dict[str, int] = defaultdict(int)
    for row_labels in labels:
        for lab in set(row_labels):
            support[lab] += 1
    target = {lab: max(1, round(holdout_fraction * count)) for lab, count in support.items()}

    keys = sorted(groups)
    random.Random(seed).shuffle(keys)
    holdout_count: dict[str, int] = defaultdict(int)
    holdout_rows: set[int] = set()
    for key in keys:
        rows = groups[key]
        group_labels = {lab for i in rows for lab in labels[i]}
        if any(holdout_count[lab] < target.get(lab, 0) for lab in group_labels):
            holdout_rows.update(rows)
            for i in rows:
                for lab in set(labels[i]):
                    holdout_count[lab] += 1

    mask = df.index.isin([df.index[i] for i in sorted(holdout_rows)])
    holdout = df[mask].reset_index(drop=True)
    train = df[~mask].reset_index(drop=True)
    stats = {
        "train_rows": int(len(train)),
        "holdout_rows": int(len(holdout)),
        "holdout_fraction_actual": round(len(holdout) / len(df), 3) if len(df) else 0.0,
    }
    return train, holdout, stats


def balance_report(
    df: pd.DataFrame,
    text_columns: list[str],  # noqa: ARG001 - kept for a uniform refine signature
    label_column: str,
    *,
    label_separator: str = ",",
) -> dict:
    """Per-label support with a min-samples recommendation for training."""
    support: dict[str, int] = defaultdict(int)
    for cell in df[label_column]:
        for lab in set(split_labels(cell, label_separator)):
            support[lab] += 1
    recommended = auto_min_samples(int(len(df)))
    ordered = dict(sorted(support.items(), key=lambda kv: kv[1], reverse=True))
    return {
        "total_rows": int(len(df)),
        "unique_labels": len(support),
        "label_support": ordered,
        "recommended_min_samples": recommended,
        "labels_below_recommended": {k: v for k, v in support.items() if v < recommended},
    }
