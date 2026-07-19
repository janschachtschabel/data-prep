"""Review (M9): filtered sample listing, status updates with atomic rewrite,
single-writer guard, and regenerate-after-discard via resume."""

from __future__ import annotations

import json

from tests.test_export import URI_ARTS, URI_OPTICS, _write_run
from tests.test_runs import _install_transport, _setup, _unique_batch_transport, _wait_for

HEADERS = {"X-API-Key": "test-key"}


def _client_with_run(make_client, tmp_path):
    client = make_client()
    _write_run(tmp_path / "runs")
    return client


# ----------------------------------------------------------------- listing ----


def test_sample_listing_filters_and_pagination(make_client, tmp_path):
    client = _client_with_run(make_client, tmp_path)

    r = client.get("/runs/run-1/samples", headers=HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 3

    assert client.get("/runs/run-1/samples?status=passed", headers=HEADERS).json()["total"] == 1
    assert client.get(f"/runs/run-1/samples?concept={URI_ARTS}", headers=HEADERS).json()["total"] == 2
    assert client.get("/runs/run-1/samples?min_sim=0.25", headers=HEADERS).json()["total"] == 1

    page = client.get("/runs/run-1/samples?offset=1&limit=1", headers=HEADERS).json()
    assert page["total"] == 3 and len(page["samples"]) == 1
    assert page["samples"][0]["id"] == "a2"

    assert client.get("/runs/missing/samples", headers=HEADERS).status_code == 404
    assert client.get("/runs/run-1/samples").status_code == 401


def test_sample_listing_tolerates_corrupted_lines(make_client, tmp_path):
    """The review browser reads the same samples.jsonl; a corrupted/partial line
    left by a crash must be skipped, not 500 the whole listing."""
    client = _client_with_run(make_client, tmp_path)
    with (tmp_path / "runs" / "run-1" / "samples.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("broken line not json\n")
    r = client.get("/runs/run-1/samples", headers=HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["total"] == 3   # the three valid rows survive, junk skipped


# ------------------------------------------------------------ status update ----


def test_status_update_rewrites_file_atomically(make_client, tmp_path):
    client = _client_with_run(make_client, tmp_path)

    r = client.post("/runs/run-1/samples/a1/status", json={"status": "discarded"}, headers=HEADERS)
    assert r.status_code == 200, r.text

    lines = [json.loads(line) for line in
             (tmp_path / "runs" / "run-1" / "samples.jsonl").read_text("utf-8").splitlines()]
    assert {s["id"]: s["status"] for s in lines} == {
        "a1": "discarded", "a2": "approved", "a3": "discarded"
    }

    assert client.post("/runs/run-1/samples/nope/status",
                       json={"status": "approved"}, headers=HEADERS).status_code == 404
    r = client.post("/runs/run-1/samples/a2/status", json={"status": "shredded"}, headers=HEADERS)
    assert r.status_code == 422  # unknown status rejected by the schema


def test_status_update_refused_while_run_is_active(make_client, tmp_path, monkeypatch):
    import app.runs as runs_module

    client = _client_with_run(make_client, tmp_path)
    monkeypatch.setattr(runs_module.run_manager, "is_active", lambda run_id: run_id == "run-1")

    r = client.post("/runs/run-1/samples/a1/status", json={"status": "approved"}, headers=HEADERS)
    assert r.status_code == 409


# --------------------------------------------------- regenerate via resume ----


def test_discard_then_resume_refills_to_target(make_client, tmp_path, monkeypatch):
    client = _setup(make_client, tmp_path, monkeypatch)
    _install_transport(monkeypatch, _unique_batch_transport())

    r = client.post("/runs", json={
        "seed_set": "s", "selection": {"mode": "list", "uris": [URI_OPTICS]},
        "per_concept": 4, "batch_size": 4,
    }, headers=HEADERS)
    run_id = r.json()["run_id"]
    final = _wait_for(client, run_id, {"completed", "failed", "paused"})
    assert final["status"] == "completed" and final["counters"]["generated"] == 4

    listing = client.get(f"/runs/{run_id}/samples", headers=HEADERS).json()
    discarded_ids = [s["id"] for s in listing["samples"][:2]]
    discarded_titles = {s["title"] for s in listing["samples"][:2]}
    for sample_id in discarded_ids:
        assert client.post(f"/runs/{run_id}/samples/{sample_id}/status",
                           json={"status": "discarded"}, headers=HEADERS).status_code == 200

    # "Nachgenerieren": resume fills the gap left by the discarded samples
    # (resume of a COMPLETED run is allowed exactly for this).
    r = client.post(f"/runs/{run_id}/resume", headers=HEADERS)
    assert r.status_code == 200, r.text
    final = _wait_for(client, run_id, {"completed", "failed", "paused"})
    assert final["status"] == "completed"

    passed = client.get(f"/runs/{run_id}/samples?status=passed", headers=HEADERS).json()
    assert passed["total"] == 4  # refilled to target
    titles = {s["title"] for s in passed["samples"]}
    assert not titles & discarded_titles  # discarded texts were not regenerated

    everything = client.get(f"/runs/{run_id}/samples", headers=HEADERS).json()
    assert everything["total"] == 6  # 4 passed + the 2 discarded stay on file