"""Generation runs: deterministic planning, gates, budget pause + resume,
cancel, and the single-run guard. All LLM traffic is mocked."""

from __future__ import annotations

import json
import time

import httpx
import pytest
import yaml

from app.vocab import parse_vocabulary
from tests.test_vocab import NESTED

URI_NATURE = "https://example.org/vocab/nature"
URI_PHYSICS = "https://example.org/vocab/physics"
URI_OPTICS = "https://example.org/vocab/optics"
URI_BIOLOGY = "https://example.org/vocab/biology"
URI_ARTS = "https://example.org/vocab/arts"

HEADERS = {"X-API-Key": "test-key"}


def _seed_payload() -> dict:
    return {
        "name": "s", "vocab": "nested", "reference": None, "per_concept_target": 2,
        "concepts": {
            uri: {"seeds": [{"title": f"Seed {i}", "description": "Ein Beispiel", "keywords": "k",
                             "source": "manual"} for i in range(2)]}
            for uri in (URI_NATURE, URI_PHYSICS, URI_OPTICS, URI_BIOLOGY, URI_ARTS)
        },
    }


# ---------------------------------------------------------------- planning ----


def test_plan_is_deterministic_and_follows_tree_order():
    from app.planning import build_plan

    vocab = parse_vocabulary(NESTED)
    a = build_plan(_seed_payload(), vocab, {"mode": "all"}, per_concept=5)
    b = build_plan(_seed_payload(), vocab, {"mode": "all"}, per_concept=5)
    assert a == b
    assert [item["concept"] for item in a] == [
        URI_NATURE, URI_PHYSICS, URI_OPTICS, URI_BIOLOGY, URI_ARTS
    ]
    assert all(item["needed"] == 5 for item in a)
    assert a[0]["label"] == "Natur"


def test_plan_subtree_and_list_selection():
    from app.planning import build_plan

    vocab = parse_vocabulary(NESTED)
    subtree = build_plan(_seed_payload(), vocab, {"mode": "subtree", "root": URI_NATURE}, per_concept=2)
    assert [i["concept"] for i in subtree] == [URI_NATURE, URI_PHYSICS, URI_OPTICS, URI_BIOLOGY]

    explicit = build_plan(_seed_payload(), vocab, {"mode": "list", "uris": [URI_ARTS, URI_OPTICS]},
                          per_concept=2)
    assert [i["concept"] for i in explicit] == [URI_OPTICS, URI_ARTS]  # tree order, not input order


def test_length_profiles_include_short_and_long():
    from app.planning import resolve_corridor

    assert resolve_corridor({"name": "kurz"}) == ("kurz", (120, 300))
    assert resolve_corridor({"name": "lang"}) == ("lang", (800, 2000))
    assert resolve_corridor({"name": "standard"}) == ("standard", (300, 600))
    assert resolve_corridor({"name": "volltext"}) == ("volltext", (3000, 6000))


def test_subtree_root_must_be_a_concept_with_actionable_error():
    from app.planning import select_concepts

    vocab = parse_vocabulary(NESTED)
    # The scheme URI is not a concept — the error must guide, not just say "unknown".
    with pytest.raises(ValueError, match="not a concept"):
        select_concepts(vocab, {"mode": "subtree", "root": "https://example.org/vocab/"})
    # A real concept URI still resolves normally.
    assert select_concepts(vocab, {"mode": "subtree", "root": URI_NATURE})[0] == URI_NATURE


def test_estimate_needs_no_llm_and_scales_with_plan():
    from app.planning import build_plan, estimate

    vocab = parse_vocabulary(NESTED)
    plan = build_plan(_seed_payload(), vocab, {"mode": "all"}, per_concept=10)
    est = estimate(plan, batch_size=5)
    assert est["total_target"] == 50
    assert est["estimated_calls"] == 10  # ceil(10/5) per concept x 5 concepts
    assert est["estimated_tokens"] > 0


# ------------------------------------------------------------------- gates ----


def test_length_corridor_and_dedupe_gate():
    from app.generation import accept_item
    from app.seeds import SeedItem

    seen: set[str] = set()
    counters = {"generated": 0, "discarded_length": 0, "discarded_duplicate": 0}
    corridor = (300, 600)

    ok_desc = "x" * 400
    accepted = accept_item(SeedItem(title="T1", description=ok_desc, keywords="k"),
                           corridor, seen, counters)
    assert accepted is not None
    too_short = accept_item(SeedItem(title="T2", description="kurz", keywords="k"),
                            corridor, seen, counters)
    too_long = accept_item(SeedItem(title="T3", description="y" * 700, keywords="k"),
                           corridor, seen, counters)
    duplicate = accept_item(SeedItem(title="T1", description=ok_desc, keywords="k"),
                            corridor, seen, counters)
    assert too_short is None and too_long is None and duplicate is None
    # Since M7 'generated' is counted by the worker after the SEMANTIC gates —
    # accept_item only owns the cheap gates and their discard counters.
    assert counters == {"generated": 0, "discarded_length": 2, "discarded_duplicate": 1}


def test_generation_prompt_uses_recipe_seeds_and_avoid_list():
    from app.generation import build_generation_prompt

    vocab = parse_vocabulary(NESTED)
    prompt = build_generation_prompt(
        vocab, URI_OPTICS,
        seeds=[{"title": "Linsen", "description": "Brechung", "keywords": "Optik"}],
        n=5, corridor=(300, 600), facet="Grundschule / einfache Sprache",
        avoid_titles=["Altes Beispiel"],
    )
    assert "Optik" in prompt and "Physik" in prompt  # hierarchy anchor
    assert "Linsen" in prompt  # few-shot seed
    assert "Grundschule" in prompt  # facet rotation
    assert "300" in prompt and "600" in prompt  # corridor in instructions
    assert "Altes Beispiel" in prompt  # anti prototype collapse
    assert "KEINE Personennamen" in prompt


def test_generation_prompt_includes_optional_guidance():
    from app.generation import build_generation_prompt

    vocab = parse_vocabulary(NESTED)
    common = dict(seeds=[], n=3, corridor=(300, 600), facet="Sekundarstufe I", avoid_titles=[])
    with_guidance = build_generation_prompt(
        vocab, URI_OPTICS, guidance="Nur deutsche Schulfächer der Sekundarstufe", **common
    )
    without_guidance = build_generation_prompt(vocab, URI_OPTICS, **common)
    assert "Nur deutsche Schulfächer der Sekundarstufe" in with_guidance
    assert "Vorgabe" in with_guidance
    # No stray guidance marker when the user leaves it empty.
    assert "Vorgabe" not in without_guidance


# ----------------------------------------------------------------- run E2E ----


def _unique_batch_transport(descriptions_len: int = 400, delay: float = 0.0):
    """Async mock host returning unique items per call (counter closure)."""
    counter = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        if delay:
            import asyncio

            await asyncio.sleep(delay)
        counter["n"] += 1
        body = json.loads(request.content.decode("utf-8"))
        n_requested = 4
        items = [
            {"title": f"Titel {counter['n']}-{i}", "description": "d" * descriptions_len,
             "keywords": "a, b, c"}
            for i in range(n_requested)
        ]
        content = json.dumps({"items": items})
        return httpx.Response(200, json={
            "id": "c", "object": "chat.completion", "created": 1, "model": body.get("model", "m"),
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 100, "total_tokens": 150},
        })

    return httpx.MockTransport(handler)


def _install_transport(monkeypatch, transport) -> None:
    """Give every run session the mocked transport (test seam)."""
    import app.runs as runs_module

    monkeypatch.setattr(runs_module, "_test_transport", transport)


def _write_config(tmp_path, max_calls: int) -> None:
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({
        "llm": {"bulk": {"model": "gpt-5.4-nano", "api_key_env": "TEST_LLM_KEY"}},
        "budgets": {"max_llm_calls": max_calls, "max_tokens_total": 10_000_000},
    }), encoding="utf-8")


def _setup(make_client, tmp_path, monkeypatch, *, max_calls: int = 1000, reference_rows=None):
    """Env (config file, key) must exist BEFORE the app builds its settings.

    ``reference_rows`` (CSV data lines) makes the seed build hybrid, so a term
    bank is mined from the reference.
    """
    import app.runs as runs_module
    from tests.conftest import FakeEncoder

    _write_config(tmp_path, max_calls)
    monkeypatch.setenv("DATAPREP_CONFIG_FILE", str(tmp_path / "config.yaml"))
    monkeypatch.setenv("TEST_LLM_KEY", "unit-key")
    # Never let unit tests download the real embedding model.
    monkeypatch.setattr(runs_module, "_test_encoder", FakeEncoder())
    client = make_client()
    headers = HEADERS
    r = client.post("/vocabs/import",
                    files={"file": ("nested.json", json.dumps(NESTED).encode(), "application/json")},
                    data={"name": "nested"}, headers=headers)
    assert r.status_code == 200, r.text
    build = {"name": "s", "vocab": "nested", "per_concept": 2}
    if reference_rows:
        header = ("properties.cclom:title;properties.cclom:general_description;"
                  "properties.cclom:general_keyword;properties.ccm:taxonid\n")
        csv = (header + "\n".join(reference_rows) + "\n").encode("utf-8")
        r = client.post("/references/import", files={"file": ("ref.csv", csv, "text/csv")},
                        data={"name": "ref"}, headers=headers)
        assert r.status_code == 200, r.text
        build["reference"] = "ref"
    r = client.post("/seeds/build", json=build, headers=headers)
    assert r.status_code == 200, r.text
    return client


def _wait_for(client, run_id: str, states: set[str], timeout: float = 15.0) -> dict:
    deadline = time.time() + timeout
    while time.time() < deadline:
        body = client.get(f"/runs/{run_id}", headers=HEADERS).json()
        if body["status"] in states:
            return body
        time.sleep(0.05)
    raise AssertionError(f"run did not reach {states} within {timeout}s: {body['status']}")


def test_run_completes_and_persists_samples(make_client, tmp_path, monkeypatch):
    client = _setup(make_client, tmp_path, monkeypatch)
    _install_transport(monkeypatch, _unique_batch_transport())

    r = client.post("/runs", json={
        "seed_set": "s", "selection": {"mode": "list", "uris": [URI_OPTICS, URI_ARTS]},
        "per_concept": 4, "batch_size": 4, "length_profile": {"name": "standard"},
    }, headers=HEADERS)
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]

    final = _wait_for(client, run_id, {"completed", "failed", "paused"})
    assert final["status"] == "completed", final
    assert final["counters"]["generated"] == 8
    assert final["counters"]["per_concept"] == {URI_OPTICS: 4, URI_ARTS: 4}
    assert final["usage"]["calls"] >= 2

    samples_file = tmp_path / "runs" / run_id / "samples.jsonl"
    lines = [json.loads(line) for line in samples_file.read_text("utf-8").splitlines()]
    assert len(lines) == 8
    first = lines[0]
    assert first["synthetic"] is True and first["status"] == "passed"
    assert first["concept"] in (URI_OPTICS, URI_ARTS)
    assert 300 <= len(first["description"]) <= 600

    listed = client.get("/runs", headers=HEADERS).json()["runs"]
    assert listed[0]["id"] == run_id and listed[0]["status"] == "completed"


def test_run_threads_guidance_into_params_and_prompt(make_client, tmp_path, monkeypatch):
    client = _setup(make_client, tmp_path, monkeypatch)
    seen: list[str] = []

    def _capturing() -> httpx.MockTransport:
        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.content.decode("utf-8"))
            items = [{"title": f"T{i}", "description": "d" * 400, "keywords": "a, b, c"}
                     for i in range(4)]
            return httpx.Response(200, json={
                "id": "c", "object": "chat.completion", "created": 1, "model": "m",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant",
                                         "content": json.dumps({"items": items})}}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 100, "total_tokens": 150},
            })
        return httpx.MockTransport(handler)

    _install_transport(monkeypatch, _capturing())
    r = client.post("/runs", json={
        "seed_set": "s", "selection": {"mode": "list", "uris": [URI_ARTS]},
        "per_concept": 4, "batch_size": 4, "guidance": "MAGIC-CONTEXT-XYZ",
    }, headers=HEADERS)
    run_id = r.json()["run_id"]
    final = _wait_for(client, run_id, {"completed", "failed", "paused"})

    # Persisted in params -> survives resume; and it actually reached the LLM.
    assert final["params"]["guidance"] == "MAGIC-CONTEXT-XYZ"
    assert any("MAGIC-CONTEXT-XYZ" in content for content in seen)


def test_run_uses_request_llm_key_and_never_persists_it(make_client, tmp_path, monkeypatch):
    """Open-instance mode for a background run: with NO server env key, the key
    sent via X-LLM-Key must drive the generation call, the X-LLM-Model override
    must apply, and the secret must NEVER be written to state.json on disk."""
    client = _setup(make_client, tmp_path, monkeypatch)
    monkeypatch.delenv("TEST_LLM_KEY", raising=False)  # no server-wide key at all
    seen_auth: list[str] = []
    seen_model: list[str] = []

    def _capturing() -> httpx.MockTransport:
        async def handler(request: httpx.Request) -> httpx.Response:
            seen_auth.append(request.headers.get("authorization", ""))
            seen_model.append(json.loads(request.content.decode("utf-8")).get("model", ""))
            items = [{"title": f"T{i}", "description": "d" * 400, "keywords": "a, b"}
                     for i in range(4)]
            return httpx.Response(200, json={
                "id": "c", "object": "chat.completion", "created": 1, "model": "m",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant",
                                         "content": json.dumps({"items": items})}}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 100, "total_tokens": 150},
            })
        return httpx.MockTransport(handler)

    _install_transport(monkeypatch, _capturing())
    r = client.post("/runs", json={
        "seed_set": "s", "selection": {"mode": "list", "uris": [URI_ARTS]},
        "per_concept": 4, "batch_size": 4,
    }, headers={**HEADERS, "X-LLM-Key": "byo-run-key", "X-LLM-Model": "gpt-5.4-mini"})
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]

    final = _wait_for(client, run_id, {"completed", "failed", "paused"})
    assert final["status"] == "completed", final
    assert seen_auth and all(a == "Bearer byo-run-key" for a in seen_auth)  # request key drove it
    assert all(m == "gpt-5.4-mini" for m in seen_model)  # request model override applied
    assert final["params"].get("llm_model") == "gpt-5.4-mini"  # surfaced (non-secret) in status

    state_text = (tmp_path / "runs" / run_id / "state.json").read_text("utf-8")
    assert "byo-run-key" not in state_text  # the secret is NEVER persisted to disk


def test_run_injects_mined_term_bank_into_the_prompt(make_client, tmp_path, monkeypatch):
    rows = [f"Linsengesetz;Brechung des Lichts an einer Sammellinse;Optik, Sammellinse;{URI_OPTICS}"]
    client = _setup(make_client, tmp_path, monkeypatch, reference_rows=rows)
    seen: list[str] = []

    def _capturing() -> httpx.MockTransport:
        async def handler(request: httpx.Request) -> httpx.Response:
            seen.append(request.content.decode("utf-8"))
            items = [{"title": f"T{i}", "description": "d" * 400, "keywords": "a, b, c"}
                     for i in range(4)]
            return httpx.Response(200, json={
                "id": "c", "object": "chat.completion", "created": 1, "model": "m",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant",
                                         "content": json.dumps({"items": items})}}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 100, "total_tokens": 150},
            })
        return httpx.MockTransport(handler)

    _install_transport(monkeypatch, _capturing())
    r = client.post("/runs", json={
        "seed_set": "s", "selection": {"mode": "list", "uris": [URI_OPTICS]},
        "per_concept": 4, "batch_size": 4,
    }, headers=HEADERS)
    _wait_for(client, r.json()["run_id"], {"completed", "failed", "paused"})

    # Terms mined from the reference reach the actual generation prompt.
    joined = "\n".join(seen).lower()
    assert any(t in joined for t in ("brechung", "sammellinse", "linsengesetz", "optik"))


def test_run_honours_configured_concurrency(make_client, tmp_path, monkeypatch):
    """The run fires up to `concurrency` LLM calls at once (bounded by the number
    of concepts, since batches within a concept are sequential)."""
    import asyncio

    client = _setup(make_client, tmp_path, monkeypatch)
    peak = {"cur": 0, "max": 0}

    def _tracking() -> httpx.MockTransport:
        counter = {"n": 0}

        async def handler(request: httpx.Request) -> httpx.Response:
            counter["n"] += 1
            unique = counter["n"]
            peak["cur"] += 1
            peak["max"] = max(peak["max"], peak["cur"])
            await asyncio.sleep(0.05)          # hold the call so overlaps are observable
            peak["cur"] -= 1
            items = [{"title": f"T{unique}-{i}", "description": "d" * 400, "keywords": "a, b, c"}
                     for i in range(4)]
            return httpx.Response(200, json={
                "id": "c", "object": "chat.completion", "created": 1, "model": "m",
                "choices": [{"index": 0, "finish_reason": "stop",
                             "message": {"role": "assistant",
                                         "content": json.dumps({"items": items})}}],
                "usage": {"prompt_tokens": 50, "completion_tokens": 100, "total_tokens": 150},
            })
        return httpx.MockTransport(handler)

    _install_transport(monkeypatch, _tracking())

    def run_with(concurrency: int) -> int:
        peak["max"] = 0
        # 5 NESTED concepts, one batch each -> concurrency is the only limiter.
        r = client.post("/runs", json={
            "seed_set": "s", "selection": {"mode": "all"},
            "per_concept": 4, "batch_size": 4, "concurrency": concurrency,
        }, headers=HEADERS)
        _wait_for(client, r.json()["run_id"], {"completed", "failed", "paused"})
        return peak["max"]

    assert run_with(2) == 2   # the semaphore caps below the concept count
    assert run_with(5) == 5   # scales up to all five concepts at once


def _fail_then_ok_transport(fail_first: int) -> httpx.MockTransport:
    """400 (a non-retried API error → LlmError) for the first ``fail_first``
    calls, then valid batches."""
    counter = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        if counter["n"] <= fail_first:
            return httpx.Response(400, json={"error": {"message": "bad", "type": "invalid_request_error"}})
        items = [{"title": f"T{counter['n']}-{i}", "description": "d" * 400, "keywords": "a, b, c"}
                 for i in range(4)]
        return httpx.Response(200, json={
            "id": "c", "object": "chat.completion", "created": 1, "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": json.dumps({"items": items})}}],
            "usage": {"prompt_tokens": 50, "completion_tokens": 100, "total_tokens": 150},
        })
    return httpx.MockTransport(handler)


def test_transient_batch_failure_is_retried_then_succeeds(make_client, tmp_path, monkeypatch):
    import app.runs as runs_module

    monkeypatch.setattr(runs_module, "_BATCH_BACKOFF_SECONDS", 0.01)
    client = _setup(make_client, tmp_path, monkeypatch)
    _install_transport(monkeypatch, _fail_then_ok_transport(fail_first=2))
    r = client.post("/runs", json={
        "seed_set": "s", "selection": {"mode": "list", "uris": [URI_ARTS]},
        "per_concept": 4, "batch_size": 4, "concurrency": 1,
    }, headers=HEADERS)
    final = _wait_for(client, r.json()["run_id"], {"completed", "failed", "paused"})
    assert final["status"] == "completed"        # two attempts fail, the third succeeds
    assert final["counters"]["generated"] == 4


def test_exhausted_batch_failures_skip_and_the_run_survives(make_client, tmp_path, monkeypatch):
    import app.runs as runs_module

    monkeypatch.setattr(runs_module, "_BATCH_BACKOFF_SECONDS", 0.01)
    client = _setup(make_client, tmp_path, monkeypatch)
    _install_transport(monkeypatch, _fail_then_ok_transport(fail_first=100_000))  # always fail
    r = client.post("/runs", json={
        "seed_set": "s", "selection": {"mode": "list", "uris": [URI_ARTS]},
        "per_concept": 4, "batch_size": 4, "concurrency": 1,
    }, headers=HEADERS)
    final = _wait_for(client, r.json()["run_id"], {"completed", "failed", "paused"})
    assert final["status"] == "completed"        # a dead batch must NOT fail the whole run
    assert final["counters"]["generated"] == 0
    assert final["counters"]["failed_batches"] >= 1


def test_missing_api_key_fails_the_run_with_a_clear_message(make_client, tmp_path, monkeypatch):
    client = _setup(make_client, tmp_path, monkeypatch)
    monkeypatch.delenv("TEST_LLM_KEY", raising=False)   # the key config.yaml names → fatal, not retried
    r = client.post("/runs", json={
        "seed_set": "s", "selection": {"mode": "list", "uris": [URI_ARTS]},
        "per_concept": 4, "batch_size": 4,
    }, headers=HEADERS)
    final = _wait_for(client, r.json()["run_id"], {"completed", "failed", "paused"})
    assert final["status"] == "failed"
    assert "TEST_LLM_KEY" in (final["message"] or "")


def test_terminal_failure_stops_siblings_instead_of_orphaning_them(make_client, tmp_path, monkeypatch):
    """When one worker hits a terminal wall (here a budget wall), the others must
    stop with it. asyncio.gather does NOT cancel siblings when one raises, so they
    used to keep appending to samples.jsonl as orphans — which, with a later
    resume adding a second writer set, generated ~2x the requested rows. A sibling
    may finish its one in-flight (already-paid) batch, but must not drain on toward
    the target after the run has stopped."""
    import asyncio

    import app.runs as runs_module
    from app.generation import GenBatch
    from app.llm import BudgetExceeded
    from app.seeds import SeedItem

    client = _setup(make_client, tmp_path, monkeypatch)
    calls = {"n": 0}

    async def fake_batch(session, prompt, max_output_tokens):
        calls["n"] += 1
        if calls["n"] == 1:                                  # one worker hits a terminal wall
            raise BudgetExceeded("simulated budget wall")
        await asyncio.sleep(0.01)                            # a sibling: one legit in-flight batch
        return GenBatch(items=[SeedItem(title=f"Sib {calls['n']}-{i}", description="d" * 400,
                                        keywords="a, b, c") for i in range(4)])

    monkeypatch.setattr(runs_module, "_generate_batch", fake_batch)

    # Target 40/concept: an orphaned sibling would drain to 40; a stopped one halts
    # right after its in-flight batch (<= a handful).
    r = client.post("/runs", json={
        "seed_set": "s", "selection": {"mode": "list", "uris": [URI_OPTICS, URI_ARTS]},
        "per_concept": 40, "batch_size": 4, "concurrency": 2,
    }, headers=HEADERS)
    run_id = r.json()["run_id"]

    assert _wait_for(client, run_id, {"paused", "failed", "completed"})["status"] == "paused"
    # After the run stopped, no sibling may keep generating toward the target.
    deadline = time.time() + 1.0
    while time.time() < deadline:
        generated = client.get(f"/runs/{run_id}", headers=HEADERS).json()["counters"]["generated"]
        assert generated < 12, f"a sibling kept generating after the run stopped: {generated}"
        time.sleep(0.05)


def test_resume_skips_corrupted_sample_lines(make_client, tmp_path, monkeypatch):
    """A crash mid-write can leave a corrupted/blank line in samples.jsonl; resume
    must skip it and rebuild, not crash the whole run with 'internal error'."""
    client = _setup(make_client, tmp_path, monkeypatch)
    _install_transport(monkeypatch, _unique_batch_transport())
    r = client.post("/runs", json={
        "seed_set": "s", "selection": {"mode": "list", "uris": [URI_ARTS]},
        "per_concept": 8, "batch_size": 4,
    }, headers=HEADERS)
    run_id = r.json()["run_id"]
    _wait_for(client, run_id, {"completed", "failed", "paused"})

    samples = tmp_path / "runs" / run_id / "samples.jsonl"
    with samples.open("a", encoding="utf-8") as fh:
        fh.write("this is not valid json\n\n")   # a corrupted line + a blank line

    assert client.post(f"/runs/{run_id}/resume", headers=HEADERS).status_code == 200
    final = _wait_for(client, run_id, {"completed", "failed", "paused"})
    assert final["status"] != "failed"           # corrupted line skipped, resume survived


def test_budget_pause_then_resume_completes(make_client, tmp_path, monkeypatch):
    client = _setup(make_client, tmp_path, monkeypatch, max_calls=1)  # ONE call only
    _install_transport(monkeypatch, _unique_batch_transport())

    r = client.post("/runs", json={
        "seed_set": "s", "selection": {"mode": "list", "uris": [URI_OPTICS, URI_ARTS]},
        "per_concept": 4, "batch_size": 4,
    }, headers=HEADERS)
    run_id = r.json()["run_id"]
    paused = _wait_for(client, run_id, {"paused", "completed", "failed"})
    assert paused["status"] == "paused"
    assert paused["counters"]["generated"] == 4  # one batch made it

    # Operator raises the budget in config.yaml, then resumes the SAME run.
    _write_config(tmp_path, max_calls=100)
    r = client.post(f"/runs/{run_id}/resume", headers=HEADERS)
    assert r.status_code == 200, r.text
    final = _wait_for(client, run_id, {"completed", "failed"})
    assert final["status"] == "completed"
    assert final["counters"]["generated"] == 8  # no duplicates from the resume

    samples_file = tmp_path / "runs" / run_id / "samples.jsonl"
    assert len(samples_file.read_text("utf-8").splitlines()) == 8


def test_cancel_does_not_drain_waiting_workers(make_client, tmp_path, monkeypatch):
    """With concepts > concurrency, cancelling must stop promptly: workers waiting
    on the semaphore must NOT each fire one more LLM call before noticing."""
    import app.runs as runs_module
    from tests.conftest import FakeEncoder

    _write_config(tmp_path, max_calls=100_000)
    monkeypatch.setenv("DATAPREP_CONFIG_FILE", str(tmp_path / "config.yaml"))
    monkeypatch.setenv("TEST_LLM_KEY", "unit-key")
    monkeypatch.setattr(runs_module, "_test_encoder", FakeEncoder())
    client = make_client()
    wide = {"id": "urn:w/", "title": {"de": "W"},
            "hasTopConcept": [{"id": f"urn:w/c{i}", "prefLabel": {"de": f"K{i}"}} for i in range(12)]}
    client.post("/vocabs/import", files={"file": ("w.json", json.dumps(wide).encode(), "application/json")},
                data={"name": "wide"}, headers=HEADERS)
    client.post("/seeds/build", json={"name": "w", "vocab": "wide", "per_concept": 2}, headers=HEADERS)
    _install_transport(monkeypatch, _unique_batch_transport(delay=0.1))

    r = client.post("/runs", json={
        "seed_set": "w", "selection": {"mode": "all"},
        "per_concept": 40, "batch_size": 4, "concurrency": 2,
    }, headers=HEADERS)
    run_id = r.json()["run_id"]

    deadline = time.time() + 5
    while time.time() < deadline:  # let it actually start generating, then cancel
        if client.get(f"/runs/{run_id}", headers=HEADERS).json()["counters"]["generated"] >= 4:
            break
        time.sleep(0.02)
    assert client.post(f"/runs/{run_id}/cancel", headers=HEADERS).status_code == 200

    final = _wait_for(client, run_id, {"cancelled", "completed", "failed"})
    assert final["status"] == "cancelled"
    # 12 concepts × 40 = 480 target. Only the ~2 in-flight batches may finish after
    # cancel — the 10 waiting workers must bail without generating.
    assert final["counters"]["generated"] < 40


def test_cancel_stops_run_and_second_start_conflicts(make_client, tmp_path, monkeypatch):
    client = _setup(make_client, tmp_path, monkeypatch)
    _install_transport(monkeypatch, _unique_batch_transport(delay=0.15))

    r = client.post("/runs", json={
        "seed_set": "s", "selection": {"mode": "all"}, "per_concept": 40, "batch_size": 4,
    }, headers=HEADERS)
    run_id = r.json()["run_id"]

    conflict = client.post("/runs", json={
        "seed_set": "s", "selection": {"mode": "all"}, "per_concept": 2,
    }, headers=HEADERS)
    assert conflict.status_code == 409

    assert client.post(f"/runs/{run_id}/cancel", headers=HEADERS).status_code == 200
    final = _wait_for(client, run_id, {"cancelled", "completed"})
    assert final["status"] == "cancelled"
    assert final["counters"]["generated"] < 200  # stopped well before the target


def test_runs_list_can_be_limited_and_reports_total(make_client, tmp_path):
    client = make_client()
    runs_dir = tmp_path / "runs"
    for i in range(12):
        directory = runs_dir / f"run-{i:02d}"
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "state.json").write_text(json.dumps({
            "id": f"run-{i:02d}", "created_at": f"2026-07-11T00:00:{i:02d}",
            "status": "completed", "params": {"target": 10},
            "counters": {"generated": 5}, "message": None,
        }), encoding="utf-8")

    body = client.get("/runs?limit=10", headers=HEADERS).json()
    assert len(body["runs"]) == 10 and body["total"] == 12
    assert body["runs"][0]["id"] == "run-11"                     # newest first
    assert len(client.get("/runs", headers=HEADERS).json()["runs"]) == 12  # no cap without limit


def test_reconcile_marks_stale_running_as_interrupted(tmp_path):
    """A crash/restart leaves runs as 'running' with no worker; startup must turn
    those into resumable 'interrupted', leaving terminal runs alone."""
    from app.run_store import reconcile_interrupted
    from app.settings import Settings

    runs_dir = tmp_path / "runs"

    def _write(rid: str, status: str) -> None:
        directory = runs_dir / rid
        directory.mkdir(parents=True, exist_ok=True)
        (directory / "state.json").write_text(json.dumps({
            "id": rid, "created_at": "2026-07-11T00:00:00", "status": status,
            "params": {"target": 10}, "counters": {"generated": 5}, "message": None,
        }), encoding="utf-8")

    _write("stale", "running")
    _write("done", "completed")
    assert reconcile_interrupted(Settings(auth_key=None, runs_dir=runs_dir)) == 1

    stale = json.loads((runs_dir / "stale" / "state.json").read_text(encoding="utf-8"))
    assert stale["status"] == "interrupted" and "restart" in stale["message"].lower()
    done = json.loads((runs_dir / "done" / "state.json").read_text(encoding="utf-8"))
    assert done["status"] == "completed"  # terminal runs untouched


def test_load_vocab_rejects_path_traversal(tmp_path):
    """The run worker loads its vocab by name from run params; a traversal name
    must be rejected in the loader (defense in depth), mirroring how the store
    and the seeds route already sanitize internally."""
    from fastapi import HTTPException

    from app.run_context import load_vocab
    from app.settings import Settings

    settings = Settings(auth_key=None, data_dir=tmp_path / "data")
    with pytest.raises(HTTPException) as exc:
        load_vocab(settings, "../evil")
    assert exc.value.status_code == 400


def test_run_routes_require_auth(make_client):
    client = make_client()
    assert client.get("/runs").status_code == 401
