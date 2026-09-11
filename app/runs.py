"""Run store + the single background generation worker.

Layout per run: ``runs/<id>/state.json`` (authoritative status/counters,
written atomically) and ``runs/<id>/samples.jsonl`` (append-only accepted
samples). Resume rebuilds the dedupe set and per-concept counts from
``samples.jsonl`` (see :mod:`app.run_context`), so a crash never loses more
than the last state flush.
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

from .generation import FACETS, GenBatch, accept_item, build_generation_prompt
from .llm import BudgetExceeded, LlmConfigError, LlmError, LlmOverride, LlmSession
from .run_context import RunContext, prepare_run
from .run_store import read_state, run_dir, write_state
from .settings import Settings
from .terms import TERMS_PER_BATCH

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
        ctx = prepare_run(settings, state, self._override, transport=_test_transport, encoder=_test_encoder)
        params = state["params"]
        concurrency = int(params.get("concurrency") or settings.generation_concurrency)
        semaphore = asyncio.Semaphore(concurrency)
        # Tripped when ANY worker fails: the others then stop starting new batches,
        # so a terminal error in one concept can't leave the siblings generating
        # (orphaned by asyncio.gather) all the way to the target.
        stop = asyncio.Event()
        # A failing worker records its error and trips `stop`; the siblings then
        # halt after their in-flight batch. We await EVERY worker (so none survives
        # the run as an orphan writer) and re-raise the first failure afterwards so
        # _execute maps it to the right status (budget -> paused, else -> failed).
        errors: list[Exception] = []

        async def guarded(uri: str) -> None:
            try:
                await self._concept_worker(settings, state, ctx, uri, semaphore, stop)
            except Exception as exc:  # any failure stops the siblings; re-raised below
                errors.append(exc)
                stop.set()

        await asyncio.gather(*(guarded(uri) for uri in params["concepts"]))
        if errors:
            raise errors[0]

    async def _concept_worker(
        self, settings: Settings, state: dict, ctx: RunContext, uri: str,
        semaphore: asyncio.Semaphore, stop: asyncio.Event,
    ) -> None:
        params = state["params"]
        concept_data = ctx.seed_payload["concepts"].get(uri, {})
        pool = concept_data.get("seeds", [])
        term_bank = concept_data.get("terms", [])
        rng = random.Random(f"{state['id']}:{uri}")  # reproducible few-shot/term rotation
        max_batches = max(1, math.ceil(ctx.per_target / ctx.batch_size)) * _MAX_BATCH_FACTOR
        batches = 0
        while ctx.per_concept[uri] < ctx.per_target and batches < max_batches:
            if self._cancel or stop.is_set():
                return
            batches += 1
            needed = ctx.per_target - ctx.per_concept[uri]
            prompt = build_generation_prompt(
                ctx.vocab, uri,
                seeds=rng.sample(pool, min(4, len(pool))) if pool else [],
                n=min(ctx.batch_size, needed),
                corridor=ctx.corridor,
                facet=FACETS[(batches - 1) % len(FACETS)],
                avoid_titles=ctx.titles[uri],
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
                    ctx.session, prompt,
                    min(16000, 500 + ctx.batch_size * max(300, ctx.corridor[1] // 2)),
                )
            if batch is None:  # transient failures exhausted — skip it, keep the run alive
                state["counters"]["failed_batches"] += 1
                write_state(settings, state)
                continue
            self._accept_batch(state, ctx, uri, batch, batches)
            state["usage"] = ctx.session.usage.as_dict()
            write_state(settings, state)

    @staticmethod
    def _accept_batch(state: dict, ctx: RunContext, uri: str, batch: GenBatch, batch_no: int) -> None:
        """Run every item of one batch through the gates and append the kept
        ones to the samples file; counters record each discard reason."""
        with ctx.samples_path.open("a", encoding="utf-8") as fh:
            for item in batch.items:
                if ctx.per_concept[uri] >= ctx.per_target:
                    break
                sample = accept_item(item, ctx.corridor, ctx.seen, state["counters"])
                if sample is None:
                    continue
                ok, reason, sim_max = ctx.gate.check(f"{sample['title']} {sample['description']}")
                if not ok:
                    state["counters"][f"discarded_{reason}"] += 1
                    continue
                state["counters"]["generated"] += 1
                sample.update(
                    {"concept": uri, "length_profile": state["params"]["length_profile"],
                     "meta": {"model": ctx.llm_model, "batch": batch_no,
                              "sim_max": round(sim_max, 4)}}
                )
                fh.write(json.dumps(sample, ensure_ascii=False) + "\n")
                ctx.per_concept[uri] += 1
                ctx.titles[uri].append(sample["title"])


run_manager = RunManager()
