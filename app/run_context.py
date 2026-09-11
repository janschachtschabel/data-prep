"""What a generation run needs loaded before its first LLM call.

Split out of :mod:`app.runs` (audit 2026-09-11, A1): loading the vocabulary,
seed set, config, LLM session and semantic gate, then replaying
``samples.jsonl`` for a resume, is one responsibility -- HOW the run then
executes (workers, retries, cancellation) is another and stays with the
:class:`app.runs.RunManager`. The test seams for the transport and the
encoder live in ``app.runs``; the manager passes them in.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from pathlib import Path

import httpx

from .config import load_config
from .embeddings import get_encoder
from .filtering import SemanticGate
from .llm import LlmOverride, LlmSession, Usage, apply_override, process_ledger
from .reference import load_reference
from .run_store import run_dir, write_state
from .samples import read_samples
from .security import safe_name
from .seeds import load_seed_set
from .settings import Settings
from .vocab import Vocabulary, parse_vocabulary


@dataclass
class RunContext:
    """Everything the concept workers share for one run."""

    vocab: Vocabulary
    seed_payload: dict
    session: LlmSession
    llm_model: str
    gate: SemanticGate
    corridor: tuple[int, int]
    per_target: int
    batch_size: int
    samples_path: Path
    # Resume-safe state rebuilt from the append-only samples file.
    seen: set[str] = field(default_factory=set)
    per_concept: dict[str, int] = field(default_factory=dict)
    titles: dict[str, list[str]] = field(default_factory=dict)


def load_vocab(settings: Settings, name: str) -> Vocabulary:
    path = settings.data_dir / "vocabs" / f"{safe_name(name, 'vocabulary name')}.json"
    if not path.exists():
        raise RuntimeError(f"Vocabulary {name!r} disappeared.")
    return parse_vocabulary(json.loads(path.read_text(encoding="utf-8")))


def prepare_run(
    settings: Settings,
    state: dict,
    override: LlmOverride | None,
    *,
    transport: httpx.AsyncBaseTransport | None,
    encoder: object | None,
) -> RunContext:
    """Load inputs, apply the per-request LLM override, build session and gate,
    and replay the samples file so a resume continues where it stopped.

    ``transport`` and ``encoder`` are the test seams (MockTransport /
    FakeEncoder); production passes ``None`` for both.
    """
    params = state["params"]
    vocab = load_vocab(settings, params["vocab"])
    seed_payload = load_seed_set(settings, params["seed_set"])
    if seed_payload is None:
        raise RuntimeError(f"Seed set {params['seed_set']!r} disappeared.")
    cfg = load_config(settings.config_file)
    endpoint = cfg.llm.get("bulk")
    if endpoint is None:
        raise RuntimeError("No 'bulk' LLM endpoint configured.")
    # Apply the per-request override (open-instance mode): the request model
    # replaces config's, the request key is threaded onto the session. The
    # KEY stays in memory; only the effective MODEL (non-secret) is recorded
    # in params so /runs status shows what actually ran.
    endpoint, api_key = apply_override(endpoint, override)
    params["llm_model"] = endpoint.model
    write_state(settings, state)
    session = LlmSession(
        endpoint=endpoint, budgets=cfg.budgets,
        usage=Usage(**state["usage"]), ledger=process_ledger(cfg.budgets),
        api_key=api_key, transport=transport,
    )

    # Semantic gates (M7): leakage index from the (optional) reference set,
    # growing dedupe index. Hybrid = the seed set names a reference.
    reference_texts: list[str] | None = None
    if seed_payload.get("reference"):
        loaded = load_reference(settings, seed_payload["reference"])
        if loaded is None:
            raise RuntimeError(f"Reference set {seed_payload['reference']!r} disappeared.")
        ref_df, ref_meta = loaded
        title_col, desc_col, *_ = ref_meta["text_columns"]
        reference_texts = [
            text for text in
            (f"{t} {d}".strip() for t, d in zip(ref_df[title_col], ref_df[desc_col], strict=False))
            if text
        ]
    encoder = encoder if encoder is not None else get_encoder(cfg.embeddings.model)
    gate = SemanticGate(
        encoder.encode,  # type: ignore[attr-defined]
        reference_texts=reference_texts,
        leakage_threshold=cfg.embeddings.leakage_threshold,
        dedupe_threshold=cfg.embeddings.dedupe_threshold,
    )
    for key in ("discarded_leakage", "discarded_semantic_duplicate", "failed_batches"):
        state["counters"].setdefault(key, 0)

    ctx = RunContext(
        vocab=vocab, seed_payload=seed_payload, session=session, llm_model=endpoint.model, gate=gate,
        corridor=(params["corridor"][0], params["corridor"][1]),
        per_target=params["per_concept"], batch_size=params["batch_size"],
        samples_path=run_dir(settings, state["id"]) / "samples.jsonl",
        per_concept=dict.fromkeys(params["concepts"], 0),
        titles={uri: [] for uri in params["concepts"]},
    )
    _replay_samples(ctx, state)
    return ctx


def _replay_samples(ctx: RunContext, state: dict) -> None:
    """Rebuild the resume-safe context from the append-only samples file.

    read_samples skips blank/corrupted lines from a crash. Exact-dedupe
    fingerprint, avoid-title list and the semantic index cover EVERY row
    (kept or discarded) so a discarded sample is never regenerated; only
    KEPT samples count towards the target, so discarding one in review
    reopens a slot this resume refills."""
    existing_texts: list[str] = []
    for sample in read_samples(ctx.samples_path):
        ctx.seen.add(f"{sample['title']}\n{sample['description']}".lower())
        ctx.titles.setdefault(sample["concept"], []).append(sample["title"])
        existing_texts.append(f"{sample['title']} {sample['description']}")
        if sample.get("status") in ("passed", "approved"):
            ctx.per_concept[sample["concept"]] = ctx.per_concept.get(sample["concept"], 0) + 1
    ctx.gate.prime(existing_texts)
    state["counters"]["per_concept"] = ctx.per_concept
    state["counters"]["generated"] = sum(ctx.per_concept.values())
