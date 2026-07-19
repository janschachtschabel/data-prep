"""Refine label audit: use a trained api_v3 model as a SECOND OPINION.

Rows where the model confidently disagrees with the gold label land on a
review checklist for the editorial team — this operationalizes the project's
Elektrotechnik finding (a subject the model predicts confidently but that has a
low F1 because of inconsistent gold labels). It NEVER auto-relabels: the human
decides.
"""

from __future__ import annotations


def audit_predictions(
    gold: list[set[str]],
    predictions: list[list[dict]],
    *,
    confidence_threshold: float = 0.8,
    top_k: int = 3,
) -> list[dict]:
    """Flag rows where the top prediction is confident (>= threshold) AND no gold
    label appears in the model's top-k ranked predictions.

    ``predictions[i]`` is the ranked list ``[{"uri", "confidence"}, ...]`` for
    row ``i``. Returns one checklist entry per flagged row.
    """
    flagged: list[dict] = []
    for index, (gold_labels, ranked) in enumerate(zip(gold, predictions, strict=False)):
        if not ranked:
            continue
        top = ranked[0]
        top_uris = [p["uri"] for p in ranked[:top_k]]
        if float(top.get("confidence", 0.0)) >= confidence_threshold and not (gold_labels & set(top_uris)):
            flagged.append({
                "index": index,
                "gold": sorted(gold_labels),
                "top_prediction": top["uri"],
                "top_confidence": round(float(top.get("confidence", 0.0)), 4),
                "model_top": [p["uri"] for p in ranked],
            })
    return flagged
