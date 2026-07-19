"""Stage 4 — deterministic generation plan: concept selection (all / subtree /
explicit list), per-concept targets, and a dry-run cost estimate that never
touches the LLM (cost-control requirement: estimate BEFORE spending)."""

from __future__ import annotations

import math

from .vocab import Vocabulary

# Named description-length corridors in characters; "frei" arrives as explicit
# min/max and becomes profile name "custom".
LENGTH_PROFILES: dict[str, tuple[int, int]] = {
    "kurz": (120, 300),
    "standard": (300, 600),
    "lang": (800, 2000),
    "volltext": (3000, 6000),
}

# Crude per-call token estimate for the dry run, calibrated on the measured
# seed bootstrap (~650 tokens for 3 short items -> ~2000 for a full batch).
_TOKENS_PER_CALL = 2000


def resolve_corridor(profile: dict | None) -> tuple[str, tuple[int, int]]:
    """Resolve a length-profile request into ``(name, (min, max))``."""
    profile = profile or {}
    name = profile.get("name")
    if not name:
        name = "custom" if (profile.get("min") or profile.get("max")) else "standard"
    if name in LENGTH_PROFILES:
        low, high = LENGTH_PROFILES[name]
    else:
        low, high = int(profile.get("min") or 100), int(profile.get("max") or 600)
        name = "custom"
    if low >= high:
        raise ValueError("Length profile: min must be smaller than max.")
    return name, (low, high)


def select_concepts(vocab: Vocabulary, selection: dict | None) -> list[str]:
    """Resolve the concept selection, always in vocabulary tree order — the
    stable ordering is what makes plans (and their cost estimates) deterministic."""
    mode = (selection or {}).get("mode", "all")
    tree_order = [uri for uri, _ in vocab.tree()]
    if mode == "all":
        return tree_order
    if mode == "subtree":
        root = str((selection or {}).get("root") or "").strip()
        if root not in vocab.concepts:
            raise ValueError(
                f"Subtree root {root!r} is not a concept in this vocabulary. Enter a concept "
                "URI from it (see the Vocabularies tab), or choose 'all' for the whole vocabulary."
            )
        wanted = set(vocab.subtree(root))
        return [uri for uri in tree_order if uri in wanted]
    if mode == "list":
        wanted = set((selection or {}).get("uris") or [])
        unknown = wanted - set(vocab.concepts)
        if unknown:
            raise ValueError(f"Selection contains {len(unknown)} unknown concept(s).")
        return [uri for uri in tree_order if uri in wanted]
    raise ValueError(f"Unknown selection mode {mode!r} (use all, subtree or list).")


def build_plan(seed_payload: dict, vocab: Vocabulary, selection: dict | None, *, per_concept: int) -> list[dict]:
    """One plan item per selected concept: label, available seeds, target count."""
    plan: list[dict] = []
    for uri in select_concepts(vocab, selection):
        pool = seed_payload["concepts"].get(uri, {}).get("seeds", [])
        plan.append(
            {"concept": uri, "label": vocab.label(uri), "seed_count": len(pool), "needed": per_concept}
        )
    return plan


def estimate(plan: list[dict], *, batch_size: int) -> dict:
    """LLM-free cost estimate for the dry run."""
    calls = sum(math.ceil(item["needed"] / batch_size) for item in plan)
    return {
        "total_target": sum(item["needed"] for item in plan),
        "estimated_calls": calls,
        "estimated_tokens": calls * _TOKENS_PER_CALL,
    }
