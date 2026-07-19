"""End-to-end acceptance runs (plan criteria 2 + 3), fully deterministic with
the LLM transport and the embedding encoder mocked.

Criterion 2 — vocab-only: vocabulary → vocab-only seed set → generation over a
hierarchy subtree → CSV (api_v3 schema, source=synthetic) + audit (mandatory
warning block).

Criterion 3 — hybrid: reference CSV with planted PII (scrubbed on import) →
hybrid seed set → a run whose LLM emits a near-copy of a reference row → the
leakage filter rejects it, and neither the plaintext PII nor the near-copy
reaches the export.
"""

from __future__ import annotations

import json

import httpx

from tests.conftest import FakeEncoder
from tests.test_runs import (
    URI_BIOLOGY,
    URI_NATURE,
    URI_OPTICS,
    URI_PHYSICS,
    _wait_for,
    _write_config,
)
from tests.test_vocab import NESTED

HEADERS = {"X-API-Key": "test-key"}

TITLE = "properties.cclom:title"
DESC = "properties.cclom:general_description"
KEYW = "properties.cclom:general_keyword"
LABEL = "properties.ccm:taxonid"

_LONG = "wort " * 80  # ~400 chars — inside the standard 300-600 corridor


def _chat_json(payload: dict) -> httpx.Response:
    return httpx.Response(200, json={
        "id": "c", "object": "chat.completion", "created": 1, "model": "m",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": json.dumps(payload)}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 9, "total_tokens": 14},
    })


def _clean_transport() -> httpx.MockTransport:
    counter = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        items = [{"title": f"Titel {counter['n']}-{i}",
                  "description": f"eintrag {counter['n']}-{i} {_LONG}", "keywords": "a, b, c"}
                 for i in range(4)]
        return _chat_json({"items": items})

    return httpx.MockTransport(handler)


def _leaky_transport() -> httpx.MockTransport:
    counter = {"n": 0}

    async def handler(request: httpx.Request) -> httpx.Response:
        counter["n"] += 1
        near_copy = {"title": f"Video zur Brechung {counter['n']}",
                     "description": f"Brechung variante {counter['n']} " + "brechung wort " * 30,
                     "keywords": "Optik"}
        clean = [{"title": f"Sauber {counter['n']}-{i}",
                  "description": f"sauber {counter['n']}-{i} {_LONG}", "keywords": "Physik"}
                 for i in range(3)]
        return _chat_json({"items": [near_copy, *clean]})

    return httpx.MockTransport(handler)


def _import_vocab(client) -> None:
    r = client.post("/vocabs/import",
                    files={"file": ("nested.json", json.dumps(NESTED).encode(), "application/json")},
                    data={"name": "nested"}, headers=HEADERS)
    assert r.status_code == 200, r.text


# ------------------------------------------------- criterion 2: vocab-only ----


def test_acceptance_vocab_only_end_to_end(make_client, tmp_path, monkeypatch):
    import app.runs as runs_module

    _write_config(tmp_path, max_calls=1000)
    monkeypatch.setenv("DATAPREP_CONFIG_FILE", str(tmp_path / "config.yaml"))
    monkeypatch.setenv("TEST_LLM_KEY", "unit-key")
    monkeypatch.setattr(runs_module, "_test_transport", _clean_transport())
    monkeypatch.setattr(runs_module, "_test_encoder", FakeEncoder())
    client = make_client()

    _import_vocab(client)
    r = client.post("/seeds/build", json={"name": "vs", "vocab": "nested", "per_concept": 2},
                    headers=HEADERS)
    assert r.status_code == 200 and r.json()["reference"] is None

    r = client.post("/runs", json={
        "seed_set": "vs", "selection": {"mode": "subtree", "root": URI_NATURE},
        "per_concept": 5, "batch_size": 4, "length_profile": {"name": "standard"},
    }, headers=HEADERS)
    run_id = r.json()["run_id"]
    final = _wait_for(client, run_id, {"completed", "failed", "paused"})
    assert final["status"] == "completed", final
    assert set(final["counters"]["per_concept"]) == {URI_NATURE, URI_PHYSICS, URI_OPTICS, URI_BIOLOGY}
    assert final["counters"]["generated"] == 20  # 4 concepts x 5

    csv = client.get(f"/runs/{run_id}/export.csv", headers=HEADERS).text
    header = csv.splitlines()[0]
    assert header == f"{TITLE};{DESC};{KEYW};{LABEL}_DISPLAYNAME;{LABEL};source"
    assert csv.strip().count("\n") == 20  # 20 data rows
    assert ";synthetic" in csv

    audit = client.get(f"/runs/{run_id}/audit.md", headers=HEADERS).text
    assert "Nutzungshinweise" in audit and "nur mit echten" in audit


# ------------------------------------------- criterion 3: hybrid + leakage ----


def _hybrid_reference() -> bytes:
    header = f"{TITLE};{DESC};{KEYW};{LABEL}\n"
    row = f"Linsen;Versuche zur Brechung, Kontakt kontakt@schule.de;Optik;{URI_PHYSICS}"
    return (header + row + "\n").encode("utf-8")


def test_acceptance_hybrid_leakage_and_pii(make_client, tmp_path, monkeypatch):
    import app.runs as runs_module

    _write_config(tmp_path, max_calls=1000)
    monkeypatch.setenv("DATAPREP_CONFIG_FILE", str(tmp_path / "config.yaml"))
    monkeypatch.setenv("TEST_LLM_KEY", "unit-key")
    monkeypatch.setattr(runs_module, "_test_transport", _leaky_transport())
    monkeypatch.setattr(runs_module, "_test_encoder", FakeEncoder())
    client = make_client()

    _import_vocab(client)
    r = client.post("/references/import",
                    files={"file": ("ref.csv", _hybrid_reference(), "text/csv")},
                    data={"name": "physik-ref"}, headers=HEADERS)
    assert r.status_code == 200 and r.json()["pii"]["counts"]["email"] == 1

    r = client.post("/seeds/build",
                    json={"name": "hs", "vocab": "nested", "reference": "physik-ref", "per_concept": 4},
                    headers=HEADERS)
    assert r.status_code == 200
    seeds = client.get("/seeds/hs", headers=HEADERS).json()
    # PII appears in NO seed (distilled from the scrubbed reference).
    assert "kontakt@schule.de" not in json.dumps(seeds, ensure_ascii=False)

    r = client.post("/runs", json={
        "seed_set": "hs", "selection": {"mode": "list", "uris": [URI_PHYSICS]},
        "per_concept": 6, "batch_size": 4,
    }, headers=HEADERS)
    run_id = r.json()["run_id"]
    final = _wait_for(client, run_id, {"completed", "failed", "paused"})
    assert final["status"] == "completed", final
    assert final["counters"]["discarded_leakage"] >= 1  # planted near-copy rejected
    assert final["counters"]["generated"] == 6

    csv = client.get(f"/runs/{run_id}/export.csv", headers=HEADERS).text
    assert "kontakt@schule.de" not in csv  # PII never reaches the export
    assert "Brechung" not in csv and "brechung" not in csv  # near-copy rejected
