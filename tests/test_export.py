"""Exporter (M8): separation guarantee, api_v3-schema CSV, audit markdown,
download routes and the guarded api_v3 push (transport mocked)."""

from __future__ import annotations

import ast
import json
from pathlib import Path

import httpx
import yaml

from app.vocab import parse_vocabulary
from tests.test_vocab import NESTED

HEADERS = {"X-API-Key": "test-key"}
URI_OPTICS = "https://example.org/vocab/optics"
URI_ARTS = "https://example.org/vocab/arts"

TITLE = "properties.cclom:title"
DESC = "properties.cclom:general_description"
KEYW = "properties.cclom:general_keyword"
LABEL = "properties.ccm:taxonid"


def _write_run(runs_dir: Path, run_id: str = "run-1") -> Path:
    run_dir = runs_dir / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    state = {
        "id": run_id, "created_at": "2026-07-11T00:00:00+00:00", "status": "completed",
        "params": {"seed_set": "s", "vocab": "nested", "concepts": [URI_OPTICS, URI_ARTS],
                   "per_concept": 2, "batch_size": 2, "length_profile": "standard",
                   "corridor": [300, 600], "target": 4},
        "counters": {"generated": 2, "discarded_length": 1, "discarded_duplicate": 0,
                     "discarded_leakage": 1, "discarded_semantic_duplicate": 0,
                     "per_concept": {URI_OPTICS: 1, URI_ARTS: 1}},
        "usage": {"calls": 3, "tokens_total": 4200},
        "message": None,
    }
    (run_dir / "state.json").write_text(json.dumps(state), encoding="utf-8")
    samples = [
        {"id": "a1", "title": "Optik Video", "description": "Brechung erklärt für Einsteiger",
         "keywords": "Optik, Licht", "synthetic": True, "status": "passed",
         "created_at": "2026-07-11T00:00:01+00:00", "concept": URI_OPTICS,
         "length_profile": "standard", "meta": {"model": "m", "batch": 1, "sim_max": 0.10}},
        {"id": "a2", "title": "Farben Quiz", "description": "Zehn Fragen zur Farbmischung",
         "keywords": "Farbe, Kunst", "synthetic": True, "status": "approved",
         "created_at": "2026-07-11T00:00:02+00:00", "concept": URI_ARTS,
         "length_profile": "standard", "meta": {"model": "m", "batch": 1, "sim_max": 0.30}},
        {"id": "a3", "title": "Verworfen", "description": "Nie exportieren",
         "keywords": "x", "synthetic": True, "status": "discarded",
         "created_at": "2026-07-11T00:00:03+00:00", "concept": URI_ARTS,
         "length_profile": "standard", "meta": {"model": "m", "batch": 1, "sim_max": 0.20}},
    ]
    (run_dir / "samples.jsonl").write_text(
        "\n".join(json.dumps(s, ensure_ascii=False) for s in samples) + "\n", encoding="utf-8"
    )
    return run_dir


# ------------------------------------------------------------- separation ----


def test_exporter_module_never_touches_reference_data():
    """Structural pin of the separation guarantee: the exporter has no import
    path to reference data — reference rows can never reach an export."""
    source = Path("app/exporter.py").read_text(encoding="utf-8")
    tree = ast.parse(source)
    imported: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.ImportFrom):
            imported.append(node.module or "")
            imported.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.Import):
            imported.extend(alias.name for alias in node.names)
    assert not any("reference" in name for name in imported), imported


def test_load_samples_reads_only_exportable_statuses(tmp_path):
    from app.exporter import load_samples

    run_dir = _write_run(tmp_path / "runs")
    samples = load_samples(run_dir)
    assert [s["id"] for s in samples] == ["a1", "a2"]  # 'discarded' never exports


def test_load_samples_tolerates_corrupted_lines(tmp_path):
    """A crash mid-write can leave a corrupted/partial line in samples.jsonl; the
    exporter — shared by export.csv, export.jsonl, audit and push — must skip it,
    not raise (such a line otherwise 500s every export/push of the run)."""
    from app.exporter import load_samples

    run_dir = _write_run(tmp_path / "runs")
    with (run_dir / "samples.jsonl").open("a", encoding="utf-8") as fh:
        fh.write("this is not valid json\n\n")   # a corrupted line + a blank line
    assert [s["id"] for s in load_samples(run_dir)] == ["a1", "a2"]  # junk skipped, valid kept


# -------------------------------------------------------------- csv/audit ----


def test_csv_uses_api_v3_schema_with_displayname(tmp_path):
    from app.exporter import load_samples, to_csv

    run_dir = _write_run(tmp_path / "runs")
    vocab = parse_vocabulary(NESTED)
    csv_text = to_csv(load_samples(run_dir), vocab)

    lines = csv_text.strip().splitlines()
    assert lines[0] == f"{TITLE};{DESC};{KEYW};{LABEL}_DISPLAYNAME;{LABEL};source"
    assert lines[1].startswith("Optik Video;Brechung erklärt für Einsteiger;Optik, Licht".replace(";", ";")[:20])
    assert "Optik;" in lines[1]  # DISPLAYNAME resolved from the vocabulary
    assert lines[1].endswith("synthetic")
    assert "Verworfen" not in csv_text
    # Arts has no German label — falls back to English per vocab.label.
    assert "Arts;" in lines[2]


def test_audit_markdown_contains_warning_and_stats(tmp_path):
    from app.exporter import audit_markdown, load_samples

    run_dir = _write_run(tmp_path / "runs")
    state = json.loads((run_dir / "state.json").read_text("utf-8"))
    vocab = parse_vocabulary(NESTED)
    md = audit_markdown(state, load_samples(run_dir), vocab)

    assert "Nutzungshinweise" in md  # mandatory distribution-shift warning block
    assert "nur mit echten" in md
    assert "run-1" in md and "4200" in md
    assert "Optik" in md  # per-concept table uses labels
    assert "0.2" in md  # mean sim_max of exportable samples (0.10, 0.30)
    assert "discarded_leakage" in md or "Leakage" in md


# ------------------------------------------------------------------ routes ----


def _client_with_run(make_client, tmp_path, monkeypatch, api_v3_url: str = ""):
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({
        "llm": {"bulk": {"model": "gpt-5.4-nano", "api_key_env": "TEST_LLM_KEY"}},
        "api_v3": {"url": api_v3_url, "api_key_env": "TEST_APIV3_KEY"},
    }), encoding="utf-8")
    monkeypatch.setenv("DATAPREP_CONFIG_FILE", str(tmp_path / "config.yaml"))
    monkeypatch.setenv("TEST_APIV3_KEY", "apiv3-secret")
    client = make_client()
    _write_run(tmp_path / "runs")
    client.post("/vocabs/import",
                files={"file": ("nested.json", json.dumps(NESTED).encode(), "application/json")},
                data={"name": "nested"}, headers=HEADERS)
    return client


def test_export_routes_require_auth_and_download(make_client, tmp_path, monkeypatch):
    client = _client_with_run(make_client, tmp_path, monkeypatch)

    assert client.get("/runs/run-1/export.csv").status_code == 401

    r = client.get("/runs/run-1/export.csv", headers=HEADERS)
    assert r.status_code == 200, r.text
    assert "attachment" in r.headers["content-disposition"]
    assert TITLE in r.text and "synthetic" in r.text

    r = client.get("/runs/run-1/export.jsonl", headers=HEADERS)
    assert r.status_code == 200
    assert len(r.text.strip().splitlines()) == 2

    r = client.get("/runs/run-1/audit.md", headers=HEADERS)
    assert r.status_code == 200
    assert "Nutzungshinweise" in r.text

    assert client.get("/runs/missing/export.csv", headers=HEADERS).status_code == 404


def test_push_uploads_csv_to_configured_api_v3(make_client, tmp_path, monkeypatch):
    import app.apiv3 as apiv3

    client = _client_with_run(make_client, tmp_path, monkeypatch,
                              api_v3_url="http://127.0.0.1:8021")

    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["key"] = request.headers.get("X-API-Key")
        captured["body"] = request.content
        return httpx.Response(200, json={"stored": "dataset.csv"})

    monkeypatch.setattr(apiv3, "_test_transport", httpx.MockTransport(handler))
    r = client.post("/runs/run-1/push", headers=HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["api_v3"] == {"stored": "dataset.csv"}
    assert captured["url"].endswith("/datasets/import")
    assert captured["key"] == "apiv3-secret"
    assert b"Optik Video" in captured["body"] and b"Verworfen" not in captured["body"]


def test_push_rejects_unconfigured_or_foreign_targets(make_client, tmp_path, monkeypatch):
    client = _client_with_run(make_client, tmp_path, monkeypatch, api_v3_url="")
    r = client.post("/runs/run-1/push", headers=HEADERS)
    assert r.status_code == 400
    assert "config" in r.json()["detail"].lower()

    client2 = _client_with_run(make_client, tmp_path, monkeypatch,
                               api_v3_url="https://evil.example.org")
    r = client2.post("/runs/run-1/push", headers=HEADERS)
    assert r.status_code == 400
    assert "not allowed" in r.json()["detail"]