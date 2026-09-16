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

import numpy as np
import pandas as pd

from ..textnorm import split_labels
from .analyze import _combined_texts, auto_min_samples
from .provenance import EXAMPLE_FOR, GENERATED_FOR, marked


def holdout_split(
    df: pd.DataFrame,
    text_columns: list[str],
    label_column: str,
    *,
    holdout_fraction: float = 0.15,
    seed: int = 42,
    label_separator: str = ",",
) -> tuple[pd.DataFrame, pd.DataFrame, dict]:
    """Split into (train, holdout, stats), text-disjoint and label-stratified.

    Rows carrying a ``generated_for`` mark stay on the TRAINING side: a holdout
    containing text an LLM wrote from the same examples the model trained on measures
    how well the model learned that LLM, and the resulting F1 flatters itself by a
    margin nobody can see afterwards. Rows marked ``example_for`` stay there too —
    they are real, but their paraphrases are in the training data.
    """
    texts = _combined_texts(df, text_columns)
    labels = [split_labels(cell, label_separator) for cell in df[label_column]]
    generated = marked(df, GENERATED_FOR)
    train_only = [g or e for g, e in zip(generated, marked(df, EXAMPLE_FOR), strict=True)]

    groups: dict[str, list[int]] = defaultdict(list)
    for i, text in enumerate(texts):
        groups[text].append(i)

    # The fraction is a fraction of the REAL rows: counting generated ones would ask
    # for a holdout larger than the real data of that label can fill.
    support: dict[str, int] = defaultdict(int)
    for row_labels, is_generated in zip(labels, generated, strict=True):
        if is_generated:
            continue
        for lab in set(row_labels):
            support[lab] += 1
    target = {lab: max(1, round(holdout_fraction * count)) for lab, count in support.items()}

    keys = sorted(groups)
    random.Random(seed).shuffle(keys)
    holdout_count: dict[str, int] = defaultdict(int)
    holdout_rows: set[int] = set()
    real_kept = 0
    for key in keys:
        rows = groups[key]
        if any(train_only[i] for i in rows):
            # Train-only. Skipping the whole GROUP rather than the single row keeps
            # the text-disjointness promise: a generated row sharing a real row's
            # text would otherwise put that text on both sides.
            real_kept += sum(1 for i in rows if not generated[i])
            continue
        group_labels = {lab for i in rows for lab in labels[i]}
        if any(holdout_count[lab] < target.get(lab, 0) for lab in group_labels):
            holdout_rows.update(rows)
            for i in rows:
                for lab in set(labels[i]):
                    holdout_count[lab] += 1

    # By POSITION: selecting by index label took every row sharing a label with a
    # holdout row, generated ones included, whenever the index was not unique.
    mask = np.zeros(len(df), dtype=bool)
    mask[sorted(holdout_rows)] = True
    holdout = df[mask].reset_index(drop=True)
    train = df[~mask].reset_index(drop=True)
    stats = {
        "train_rows": int(len(train)),
        "holdout_rows": int(len(holdout)),
        "holdout_fraction_actual": round(len(holdout) / len(df), 3) if len(df) else 0.0,
        "generated_excluded": int(sum(generated)),
        # Real rows the holdout could not have: examples of the generator, and rows
        # sharing their text with a marked one. Said, not implied.
        "real_kept_in_train": real_kept,
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
