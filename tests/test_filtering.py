"""Semantic gates (M7): leakage filter against reference texts, internal
semantic dedupe, and the planted-near-copy acceptance test (criterion 3).
Encoder and LLM are faked/mocked — no model download, no network."""

from __future__ import annotations

import json

import httpx
import yaml

from tests.conftest import FakeEncoder
from tests.test_runs import URI_PHYSICS, _wait_for
from tests.test_vocab import NESTED

HEADERS = {"X-API-Key": "test-key"}


# ------------------------------------------------------------- unit gates ----


def test_leakage_filter_rejects_near_copies_of_references():
    from app.filtering import SemanticGate

    gate = SemanticGate(
        FakeEncoder().encode,
        reference_texts=["Linsen und Licht Versuche zur Brechung mit Alltagsmaterial"],
        leakage_threshold=0.90,
        dedupe_threshold=0.95,
    )
    accepted, reason, sim = gate.check("Erklärvideo über die Brechung von Licht an Linsen")
    assert accepted is False and reason == "leakage" and sim >= 0.90

    accepted, reason, _ = gate.check("Arbeitsblatt zum Zellaufbau der Pflanzen")
    assert accepted is True and reason is None


def test_semantic_dedupe_rejects_repeats_within_a_run():
    from app.filtering import SemanticGate

    gate = SemanticGate(FakeEncoder().encode, reference_texts=None,
                        leakage_threshold=0.90, dedupe_threshold=0.95)
    assert gate.check("Quiz über Brechung")[0] is True
    accepted, reason, sim = gate.check("Anderes Quiz über Brechung im Alltag")  # same direction
    assert accepted is False and reason == "semantic_duplicate" and sim >= 0.95
    assert gate.check("Podcast zum Zellaufbau")[0] is True


def test_vocab_only_gate_has_no_leakage_index():
    from app.filtering import SemanticGate

    gate = SemanticGate(FakeEncoder().encode, reference_texts=None,
                        leakage_threshold=0.90, dedupe_threshold=0.95)
    accepted, reason, _ = gate.check("Erklärvideo über die Brechung von Licht")
    assert accepted is True and reason is None  # nothing to leak from


def test_accept_item_no_longer_counts_generated():
    """Since M7 the 'generated' counter moves to the worker — it must only
    count samples that survived ALL gates, including the semantic ones."""
    from app.generation import accept_item
    from app.seeds import SeedItem

    counters = {"generated": 0, "discarded_length": 0, "discarded_duplicate": 0}
    sample = accept_item(SeedItem(title="T", description="x" * 400, keywords="k"),
                         (300, 600), set(), counters)
    assert sample is not None
    assert counters["generated"] == 0


def test_vector_index_matches_bruteforce_max_cosine_and_grows_past_capacity():
    """The append-only index returns the same max-cosine as a brute-force stack
    and grows past its initial capacity without corruption — the O(N) replacement
    for the old per-row ``np.vstack`` re-stack (which was O(N^2))."""
    import numpy as np

    from app.embeddings import VectorIndex

    rng = np.random.default_rng(0)
    vecs = rng.standard_normal((20, 8)).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)  # normalized: cosine = dot

    index = VectorIndex()
    assert index.max_similarity(vecs[0]) == 0.0          # empty index -> 0.0
    for i, v in enumerate(vecs):
        expected = float(np.max(vecs[:i] @ v)) if i else 0.0
        assert index.max_similarity(v) == expected       # matches brute force
        index.add(v)
    assert len(index) == 20                               # grew past initial capacity
    assert index.max_similarity(vecs[5]) >= 0.999        # exact repeat -> cosine ~1.0


def test_vector_index_extend_equals_repeated_add():
    import numpy as np

    from app.embeddings import VectorIndex

    rng = np.random.default_rng(1)
    vecs = rng.standard_normal((10, 4)).astype(np.float32)
    vecs /= np.linalg.norm(vecs, axis=1, keepdims=True)
    bulk, one_by_one = VectorIndex(), VectorIndex()
    bulk.extend(vecs)
    for v in vecs:
        one_by_one.add(v)
    assert len(bulk) == len(one_by_one) == 10
    assert bulk.max_similarity(vecs[3]) == one_by_one.max_similarity(vecs[3])


# ------------------------------------------- planted near-copy, full run ----


def _hybrid_setup(make_client, tmp_path, monkeypatch):
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({
        "llm": {"bulk": {"model": "gpt-5.4-nano", "api_key_env": "TEST_LLM_KEY"}},
        "budgets": {"max_llm_calls": 100, "max_tokens_total": 10_000_000},
        "embeddings": {"model": "unused-in-tests", "leakage_threshold": 0.90,
                       "dedupe_threshold": 0.95},
    }), encoding="utf-8")
    monkeypatch.setenv("DATAPREP_CONFIG_FILE", str(tmp_path / "config.yaml"))
    monkeypatch.setenv("TEST_LLM_KEY", "unit-key")
    client = make_client()

    csv_header = ("properties.cclom:title;properties.cclom:general_description;"
                  "properties.cclom:general_keyword;properties.ccm:taxonid\n")
    rows = [
        f"Linsen und Licht;Versuche zur Brechung mit Alltagsmaterial;Optik;{URI_PHYSICS}",
        f"Hebelgesetz Quiz;Zehn Aufgaben mit Auswertung;Mechanik;{URI_PHYSICS}",
    ]
    reference_csv = (csv_header + "\n".join(rows) + "\n").encode("utf-8")

    r = client.post("/vocabs/import",
                    files={"file": ("nested.json", json.dumps(NESTED).encode(), "application/json")},
                    data={"name": "nested"}, headers=HEADERS)
    assert r.status_code == 200, r.text
    r = client.post("/references/import",
                    files={"file": ("ref.csv", reference_csv, "text/csv")},
                    data={"name": "physik-ref"}, headers=HEADERS)
    assert r.status_code == 200, r.text
    r = client.post("/seeds/build",
                    json={"name": "s", "vocab": "nested", "reference": "physik-ref",
                          "per_concept": 2}, headers=HEADERS)
    assert r.status_code == 200, r.text
    return client


def _llm_with_planted_copy() -> httpx.MockTransport:
    """Every batch: one near-copy of a reference row + one unique clean item."""
    counter = {"n": 0}

    def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        items = [
            {"title": "Video zur Brechung", "description": "B" * 400, "keywords": "Optik"},
            {"title": f"Sauberes Material {counter['n']}",
             "description": "s" * 400 + str(counter["n"]), "keywords": "Physik"},
        ]
        content = json.dumps({"items": items})
        return httpx.Response(200, json={
            "id": "c", "object": "chat.completion", "created": 1, "model": "m",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": content}}],
            "usage": {"prompt_tokens": 5, "completion_tokens": 9, "total_tokens": 14},
        })

    return httpx.MockTransport(handler)


def test_hybrid_run_rejects_planted_near_copy(make_client, tmp_path, monkeypatch):
    import app.runs as runs_module

    client = _hybrid_setup(make_client, tmp_path, monkeypatch)
    monkeypatch.setattr(runs_module, "_test_transport", _llm_with_planted_copy())
    monkeypatch.setattr(runs_module, "_test_encoder", FakeEncoder())

    r = client.post("/runs", json={
        "seed_set": "s",
        "selection": {"mode": "list", "uris": [URI_PHYSICS]},
        "per_concept": 3, "batch_size": 2,
    }, headers=HEADERS)
    assert r.status_code == 200, r.text
    run_id = r.json()["run_id"]

    final = _wait_for(client, run_id, {"completed", "failed", "paused"})
    assert final["status"] == "completed", final
    assert final["counters"]["discarded_leakage"] >= 1

    lines = [json.loads(line) for line in
             (tmp_path / "runs" / run_id / "samples.jsonl").read_text("utf-8").splitlines()]
    assert lines, "clean items must still be accepted"
    joined = json.dumps(lines, ensure_ascii=False)
    assert "Brechung" not in joined  # the near-copy never reached the output
    assert all("sim_max" in sample["meta"] for sample in lines)