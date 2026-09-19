"""Refine filter operations. Each is ``df, params, ctx -> (new_df, stats)`` and
NEVER mutates the input (a fresh frame is returned). Two families:

- *drop* filters remove rows (dedupe, length, label, cap) — stats carry
  before/after/removed and a few example removed rows.
- *transform* filters rewrite text in place (markup, pii-mask) — stats carry a
  ``changed`` count and before/after example diffs.

The semantic dedupe takes an encoder from ``ctx`` (injected; tests fake it).
"""

from __future__ import annotations

from collections.abc import Callable

import numpy as np
import pandas as pd

from ..embeddings import VectorIndex
from ..pii import scrub
from ..textnorm import clean_text, split_labels
from .analyze import _combined_texts

_EXAMPLES = 5


def _combined(df: pd.DataFrame, ctx: dict) -> list[str]:
    return _combined_texts(df, ctx["text_columns"])


def _drop_stats(
    df: pd.DataFrame, keep: list[bool], name: str, removed_examples: list[str]
) -> tuple[pd.DataFrame, dict]:
    new = df[pd.Series(keep, index=df.index)].reset_index(drop=True)
    return new, {
        "filter": name, "before": int(len(df)), "after": int(len(new)),
        "removed": int(len(df) - len(new)), "changed": 0,
        "examples": [{"removed": ex} for ex in removed_examples[:_EXAMPLES]],
    }


def _dedupe_exact(df, params, ctx):
    texts = _combined(df, ctx)
    seen: set[str] = set()
    keep, examples = [], []
    for text in texts:
        dup = text in seen
        keep.append(not dup)
        if dup and len(examples) < _EXAMPLES:
            examples.append(text[:120])
        seen.add(text)
    return _drop_stats(df, keep, "dedupe_exact", examples)


def _dedupe_semantic(df, params, ctx):
    encoder = ctx.get("encoder")
    if encoder is None:
        raise ValueError("Semantic dedupe needs an embedding model (none available).")
    threshold = float(params.get("threshold", 0.95))
    texts = _combined(df, ctx)
    vectors = _normalize(np.asarray(encoder.encode(texts), dtype=np.float32))
    index = VectorIndex()
    keep, examples = [], []
    for i, vector in enumerate(vectors):
        sim = index.max_similarity(vector)
        is_dup = sim >= threshold
        keep.append(not is_dup)
        if is_dup:
            if len(examples) < _EXAMPLES:
                examples.append(texts[i][:120])
        else:
            index.add(vector)
    return _drop_stats(df, keep, "dedupe_semantic", examples)


def _length(df, params, ctx):
    low, high = int(params.get("min", 0)), int(params.get("max", 10**9))
    texts = _combined(df, ctx)
    keep = [low <= len(t) <= high for t in texts]
    examples = [t[:120] for t, k in zip(texts, keep, strict=False) if not k]
    return _drop_stats(df, keep, "length", examples)


def _drop_no_label(df, params, ctx):
    sep = ctx["label_separator"]
    labels = [split_labels(cell, sep) for cell in df[ctx["label_column"]]]
    keep = [bool(labs) for labs in labels]
    return _drop_stats(df, keep, "drop_no_label", [""] * (len(df) - sum(keep)))


def _label_filter(df, params, ctx):
    substring = params.get("substring", "")
    if not substring:
        raise ValueError("label_filter needs a 'substring' parameter.")
    sep = ctx["label_separator"]
    col = ctx["label_column"]
    # The display names go with their labels: left standing, "Chemie,Physik" beside
    # the one label kept names it wrongly wherever a prompt reads it.
    names_col = f"{col}_DISPLAYNAME"
    names = df[names_col].tolist() if names_col in df.columns else None
    new = df.copy()
    kept_rows, examples = [], []
    new_cells, new_names = [], []
    changed = 0  # rows kept with some of their labels stripped
    for index, cell in enumerate(df[col]):
        labels = split_labels(cell, sep)
        keep = [substring in lab for lab in labels]
        kept = [lab for lab, wanted in zip(labels, keep, strict=True) if wanted]
        new_cells.append(sep.join(kept))
        kept_rows.append(bool(kept))
        changed += bool(kept) and len(kept) < len(labels)
        if names is not None:
            new_names.append(names[index] if all(keep)
                             else _names_kept(names[index], keep, sep))
        if not kept and len(examples) < _EXAMPLES:
            examples.append(str(cell)[:120])
    new[col] = new_cells
    if names is not None:
        new[names_col] = new_names
    new = new[pd.Series(kept_rows, index=new.index)].reset_index(drop=True)
    return new, {
        "filter": "label_filter", "before": int(len(df)), "after": int(len(new)),
        "removed": int(len(df) - sum(kept_rows)), "changed": changed,
        "examples": [{"removed": ex} for ex in examples],
    }


def _names_kept(cell, keep: list[bool], sep: str) -> str:
    """The names of the labels ``keep`` marks, when the name cell pairs with the label
    cell part for part; otherwise none -- a guess would name a label wrongly."""
    names = split_labels(cell, sep)
    if len(names) != len(keep):
        return ""
    return sep.join(name for name, wanted in zip(names, keep, strict=True) if wanted)


def _cap_per_label(df, params, ctx):
    cap = int(params.get("cap", 0))
    if cap < 1:
        raise ValueError("cap_per_label needs a 'cap' >= 1.")
    sep = ctx["label_separator"]
    counts: dict[str, int] = {}
    keep = []
    for cell in df[ctx["label_column"]]:
        labs = split_labels(cell, sep)
        # Keep the row while any of its labels still needs samples — this trims
        # over-represented labels but preserves minority-bearing rows.
        take = any(counts.get(lab, 0) < cap for lab in labs) if labs else False
        keep.append(take)
        if take:
            for lab in labs:
                counts[lab] = counts.get(lab, 0) + 1
    return _drop_stats(df, keep, "cap_per_label", [""] * (len(df) - sum(keep)))


def _markup(df, params, ctx):
    new = df.copy()
    row_changed = pd.Series(False, index=new.index)
    examples: list[dict] = []
    for col in ctx["text_columns"]:
        cleaned = new[col].map(clean_text)
        diff = cleaned != new[col].fillna("")
        row_changed |= diff
        for idx in new.index[diff]:
            if len(examples) < _EXAMPLES:
                examples.append({"before": str(new.at[idx, col])[:120], "after": cleaned.at[idx][:120]})
        new[col] = cleaned
    return new, {
        "filter": "markup", "before": int(len(df)), "after": int(len(df)),
        "removed": 0, "changed": int(row_changed.sum()), "examples": examples,
    }


def _pii(df, params, ctx):
    mode = params.get("mode", "mask")
    if mode not in ("mask", "drop"):
        raise ValueError("pii mode must be 'mask' or 'drop'.")
    new = df.copy()
    changed, examples, keep = 0, [], []
    for idx in new.index:
        row_hit = False
        for col in ctx["text_columns"]:
            value = new.at[idx, col]
            if not isinstance(value, str) or not value:
                continue
            cleaned, found = scrub(value)
            if found:
                row_hit = True
                if mode == "mask":
                    new.at[idx, col] = cleaned
                    if len(examples) < _EXAMPLES:
                        examples.append({"before": value[:120], "after": cleaned[:120]})
        keep.append(not (row_hit and mode == "drop"))
        if row_hit and mode == "mask":
            changed += 1
    if mode == "drop":
        kept = new[pd.Series(keep, index=new.index)].reset_index(drop=True)
        return kept, {
            "filter": "pii", "before": int(len(df)), "after": int(len(kept)),
            "removed": int(len(df) - len(kept)), "changed": 0, "examples": [],
        }
    return new, {
        "filter": "pii", "before": int(len(df)), "after": int(len(new)),
        "removed": 0, "changed": changed, "examples": examples,
    }


def _normalize(vectors: np.ndarray) -> np.ndarray:
    norms = np.linalg.norm(vectors, axis=1, keepdims=True)
    norms[norms == 0.0] = 1.0
    return vectors / norms


FILTERS: dict[str, Callable[..., tuple[pd.DataFrame, dict]]] = {
    "dedupe_exact": _dedupe_exact,
    "dedupe_semantic": _dedupe_semantic,
    "length": _length,
    "drop_no_label": _drop_no_label,
    "label_filter": _label_filter,
    "cap_per_label": _cap_per_label,
    "markup": _markup,
    "pii": _pii,
}


def run_filter(name: str, df: pd.DataFrame, params: dict, ctx: dict) -> tuple[pd.DataFrame, dict]:
    """Dispatch to one filter; raises ``ValueError`` for an unknown name."""
    fn = FILTERS.get(name)
    if fn is None:
        raise ValueError(f"Unknown filter {name!r}. Available: {', '.join(sorted(FILTERS))}.")
    missing = [c for c in ctx["text_columns"] if c not in df.columns]
    if ctx["label_column"] not in df.columns:
        missing.append(ctx["label_column"])
    if missing:
        raise ValueError(f"Missing columns: {', '.join(missing)}.")
    return fn(df, params, ctx)
