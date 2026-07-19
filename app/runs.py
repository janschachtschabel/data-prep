"""Run store + the single background generation worker.

Layout per run: ``runs/<id>/state.json`` (authoritative status/counters,
written atomically) and ``runs/<id>/samples.jsonl`` (append-only accepted
samples). Resume rebuilds the dedupe set and per-concept counts from
``samples.jsonl``, so a crash never loses more than the last state flush.
Single-worker design: ONE active run per process (mirrors api_v3's training
job); concurrency lives INSIDE the run — concepts generate in parallel under a
semaphore, batches within a concept stay sequential so the anti-prototype
"avoid these titles" context grows correctly.
"""

from __future__ import annotations

import asyncio
import json
import logging
import math
import random
import uuid
from datetime import UTC, datetime

import httpx

from .config import load_config
from .embeddings import get_encoder
from .filtering import SemanticGate
from .generation import FACETS, GenBatch, accept_item, build_generation_prompt
from .llm import (
    BudgetExceeded,
    LlmConfigError,
    LlmError,
    LlmOverride,
    LlmSession,
    Usage,
    apply_override,
    process_ledger,
)
from .reference import load_reference
from .run_store import read_state, run_dir, write_state
from .samples import read_samples
from .security import safe_name
from .seeds import load_seed_set
from .settings import Settings
from .terms import TERMS_PER_BATCH
from .vocab import Vocabulary, parse_vocabulary

logger = logging.getLogger("data_prep.runs")

# Test seams: tests monkeypatch these (MockTransport / FakeEncoder) so unit
# runs never touch the network or download the embedding model.
_test_transport: httpx.AsyncBaseTransport | None = None
_test_encoder: object | None = None

_MAX_BATCH_FACTOR = 3  # hard stop per concept: 3x the theoretical batch count
_BATCH_RETRIES = 3          # transient LLM failures per batch before it is skipped
_BATCH_BACKOFF_SECONDS = 1.5


async def _generate_batch(session: LlmSession, prompt: str, max_output_tokens: int) -> GenBatch | None:
    """One generation batch, resilient to transient LLM failures (rate limits,
    5xx, timeouts, the odd unparsable response — the SDK already retries 429/5xx
    within a call; this adds a batch-level retry on top). Terminal failures
    (budget reached, missing API key) propagate so the run stops correctly; when
    transient retries are exhausted this returns ``None`` so the caller skips the
    batch and the RUN keeps going instead of failing wholesale."""
    for attempt in range(1, _BATCH_RETRIES + 1):
        try:
            return await session.complete(prompt, GenBatch, max_output_tokens=max_output_tokens)
        except (BudgetExceeded, LlmConfigError):
            raise
        except LlmError as exc:
            logger.warning("Generation batch failed (attempt %d/%d): %s", attempt, _BATCH_RETRIES, exc)
            if attempt < _BATCH_RETRIES:
                await asyncio.sleep(_BATCH_BACKOFF_SECONDS * attempt)
    return None


class RunManager:
    """Process-local manager for the one active generation run."""

    def __init__(self) -> None:
        self._task: asyncio.Task | None = None
        self._active_id: str | None = None
        self._cancel = False
        # Per-run LLM credentials from the request (open-instance mode). Held in
        # MEMORY only for the lifetime of the run — the API key is a secret and
        # is never written into params/state.json. None = fall back to the env key.
        self._override: LlmOverride | None = None

    # ---------------------------------------------------------- lifecycle ----

    def is_busy(self) -> bool:
        return self._task is not None and not self._task.done()

    def start(self, settings: Settings, params: dict, override: LlmOverride | None = None) -> str:
        if self.is_busy():
            raise RuntimeError("A run is already active — cancel it or wait for it to finish.")
        self._override = override
        run_id = datetime.now(UTC).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:6]
        state = {
            "id": run_id,
            "created_at": datetime.now(UTC).isoformat(),
            "params": params,
            "status": "running",
            "counters": {"generated": 0, "discarded_length": 0, "discarded_duplicate": 0,
                         "discarded_leakage": 0, "discarded_semantic_duplicate": 0,
                         "failed_batches": 0, "per_concept": {}},
            "usage": {"calls": 0, "tokens_total": 0},
            "message": None,
        }
        run_dir(settings, run_id).mkdir(parents=True, exist_ok=True)
        write_state(settings, state)
        self._launch(settings, state)
        return run_id

    def resume(self, settings: Settings, run_id: str, override: LlmOverride | None = None) -> None:
        if self.is_busy():
            raise RuntimeError("A run is already active.")
        state = read_state(settings, run_id)
        if state is None:
            raise KeyError(run_id)
        # The key is never persisted, so a resume after a restart must carry it
        # again (via the request header); absent that, fall back to the env key.
        self._override = override
        # 'completed' is resumable too: discarding samples in review reopens
        # slots (per_concept counts only kept samples), and resume refills them.
        if state["status"] not in ("paused", "cancelled", "failed", "completed", "interrupted"):
            raise ValueError(f"Run is {state['status']!r} and cannot be resumed.")
        state["status"] = "running"
        state["message"] = None
        write_state(settings, state)
        self._launch(settings, state)

    def is_active(self, run_id: str) -> bool:
        """True while this specific run is the one being generated right now."""
        return self._active_id == run_id and self.is_busy()

    def cancel(self, run_id: str) -> bool:
        if self.is_active(run_id):
            self._cancel = True
            return True
        return False

    def _launch(self, settings: Settings, state: dict) -> None:
        self._cancel = False
        self._active_id = state["id"]
        self._task = asyncio.create_task(self._execute(settings, state))

    # ------------------------------------------------------------- worker ----

    async def _execute(self, settings: Settings, state: dict) -> None:
        try:
            await self._generate(settings, state)
        except BudgetExceeded as exc:
            state["status"] = "paused"
            state["message"] = f"{exc} Raise the budget in config.yaml and resume."
        except LlmError as exc:
            # A terminal LLM problem the batch-level retries deliberately did not
            # swallow (e.g. a missing API key); the message is client-safe.
            logger.warning("Run %s stopped: %s", state["id"], exc)
            state["status"] = "failed"
            state["message"] = str(exc)
        except Exception:
            logger.exception("Run %s failed", state["id"])
            state["status"] = "failed"
            state["message"] = "Internal error — see server logs."
        else:
            state["status"] = "cancelled" if self._cancel else "completed"
        state["finished_at"] = datetime.now(UTC).isoformat()
        write_state(settings, state)
        # Drop the in-memory request key once the run is no longer executing —
        # a resume (paused/cancelled/failed) supplies it again via its header.
        self._override = None

    async def _generate(self, settings: Settings, state: dict) -> None:
        params = state["params"]
        vocab = self._load_vocab(settings, params["vocab"])
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
        endpoint, api_key = apply_override(endpoint, self._override)
        params["llm_model"] = endpoint.model
        write_state(settings, state)
        session = LlmSession(
            endpoint=endpoint, budgets=cfg.budgets,
            usage=Usage(**state["usage"]), ledger=process_ledger(cfg.budgets),
            api_key=api_key, transport=_test_transport,
        )
        corridor: tuple[int, int] = (params["corridor"][0], params["corridor"][1])
        per_target: int = params["per_concept"]
        batch_size: int = params["batch_size"]

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
        encoder = _test_encoder if _test_encoder is not None else get_encoder(cfg.embeddings.model)
        gate = SemanticGate(
            encoder.encode,  # type: ignore[attr-defined]
            reference_texts=reference_texts,
            leakage_threshold=cfg.embeddings.leakage_threshold,
            dedupe_threshold=cfg.embeddings.dedupe_threshold,
        )
        for key in ("discarded_leakage", "discarded_semantic_duplicate", "failed_batches"):
            state["counters"].setdefault(key, 0)

        # Rebuild resume-safe context from the append-only samples file.
        samples_path = run_dir(settings, state["id"]) / "samples.jsonl"
        seen: set[str] = set()
        per_concept: dict[str, int] = dict.fromkeys(params["concepts"], 0)
        titles: dict[str, list[str]] = {uri: [] for uri in params["concepts"]}
        existing_texts: list[str] = []
        # read_samples skips blank/corrupted lines from a crash. Exact-dedupe
        # fingerprint, avoid-title list and the semantic index cover EVERY row
        # (kept or discarded) so a discarded sample is never regenerated; only
        # KEPT samples count towards the target, so discarding one in review
        # reopens a slot this resume refills.
        for sample in read_samples(samples_path):
            seen.add(f"{sample['title']}\n{sample['description']}".lower())
            titles.setdefault(sample["concept"], []).append(sample["title"])
            existing_texts.append(f"{sample['title']} {sample['description']}")
            if sample.get("status") in ("passed", "approved"):
                per_concept[sample["concept"]] = per_concept.get(sample["concept"], 0) + 1
        gate.prime(existing_texts)
        state["counters"]["per_concept"] = per_concept
        state["counters"]["generated"] = sum(per_concept.values())

        concurrency = int(params.get("concurrency") or settings.generation_concurrency)
        semaphore = asyncio.Semaphore(concurrency)
        max_batches = max(1, math.ceil(per_target / batch_size)) * _MAX_BATCH_FACTOR
        # Tripped when ANY worker fails: the others then stop starting new batches,
        # so a terminal error in one concept can't leave the siblings generating
        # (orphaned by asyncio.gather) all the way to the target.
        stop = asyncio.Event()

        async def concept_worker(uri: str) -> None:
            concept_data = seed_payload["concepts"].get(uri, {})
            pool = concept_data.get("seeds", [])
            term_bank = concept_data.get("terms", [])
            rng = random.Random(f"{state['id']}:{uri}")  # reproducible few-shot/term rotation
            batches = 0
            while per_concept[uri] < per_target and batches < max_batches:
                if self._cancel or stop.is_set():
                    return
                batches += 1
                needed = per_target - per_concept[uri]
                prompt = build_generation_prompt(
                    vocab, uri,
                    seeds=rng.sample(pool, min(4, len(pool))) if pool else [],
                    n=min(batch_size, needed),
                    corridor=corridor,
                    facet=FACETS[(batches - 1) % len(FACETS)],
                    avoid_titles=titles[uri],
                    guidance=params.get("guidance", ""),
                    terms=rng.sample(term_bank, min(TERMS_PER_BATCH, len(term_bank))) if term_bank else None,
                )
                async with semaphore:
                    # Re-check AFTER acquiring the permit: a worker that queued here
                    # before cancel (or before a sibling's failure) must bail without
                    # firing another LLM call, otherwise every waiting worker drains
                    # one more batch.
                    if self._cancel or stop.is_set():
                        return
                    batch = await _generate_batch(
                        session, prompt,
                        min(16000, 500 + batch_size * max(300, corridor[1] // 2)),
                    )
                if batch is None:  # transient failures exhausted — skip it, keep the run alive
                    state["counters"]["failed_batches"] += 1
                    write_state(settings, state)
                    continue
                with samples_path.open("a", encoding="utf-8") as fh:
                    for item in batch.items:
                        if per_concept[uri] >= per_target:
                            break
                        sample = accept_item(item, corridor, seen, state["counters"])
                        if sample is None:
                            continue
                        ok, reason, sim_max = gate.check(f"{sample['title']} {sample['description']}")
                        if not ok:
                            state["counters"][f"discarded_{reason}"] += 1
                            continue
                        state["counters"]["generated"] += 1
                        sample.update(
                            {"concept": uri, "length_profile": params["length_profile"],
                             "meta": {"model": endpoint.model, "batch": batches,
                                      "sim_max": round(sim_max, 4)}}
                        )
                        fh.write(json.dumps(sample, ensure_ascii=False) + "\n")
                        per_concept[uri] += 1
                        titles[uri].append(sample["title"])
                state["usage"] = session.usage.as_dict()
                write_state(settings, state)

        # A failing worker records its error and trips `stop`; the siblings then
        # halt after their in-flight batch. We await EVERY worker (so none survives
        # the run as an orphan writer) and re-raise the first failure afterwards so
        # _execute maps it to the right status (budget -> paused, else -> failed).
        errors: list[Exception] = []

        async def guarded(uri: str) -> None:
            try:
                await concept_worker(uri)
            except Exception as exc:  # any failure stops the siblings; re-raised below
                errors.append(exc)
                stop.set()

        await asyncio.gather(*(guarded(uri) for uri in params["concepts"]))
        if errors:
            raise errors[0]

    @staticmethod
    def _load_vocab(settings: Settings, name: str) -> Vocabulary:
        path = settings.data_dir / "vocabs" / f"{safe_name(name, 'vocabulary name')}.json"
        if not path.exists():
            raise RuntimeError(f"Vocabulary {name!r} disappeared.")
        return parse_vocabulary(json.loads(path.read_text(encoding="utf-8")))


run_manager = RunManager()
