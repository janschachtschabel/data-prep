"""Refine part 4: stratified text-disjoint holdout split, balance report, and
the guarded api_v3 push for refine datasets (transport mocked)."""

from __future__ import annotations

import json

import httpx
import pandas as pd
import yaml

HEADERS = {"X-API-Key": "test-key"}

TITLE = "properties.cclom:title"
DESC = "properties.cclom:general_description"
KEYW = "properties.cclom:general_keyword"
LABEL = "properties.ccm:taxonid"
COLS = [TITLE, DESC, KEYW]


def _dataset() -> pd.DataFrame:
    # 4 labels, each with several DISTINCT texts, so a stratified split can put
    # some of every label on each side.
    rows = []
    for lab in ("disc/A", "disc/B", "disc/C", "disc/D"):
        for i in range(8):
            rows.append([f"Titel {lab}-{i}", f"Ein eindeutiger Beispieltext {lab} nummer {i}", "kw", lab])
    return pd.DataFrame(rows, columns=[TITLE, DESC, KEYW, LABEL])


def _combined(df):
    from app.textnorm import clean_text
    return {clean_text(f"{t} {d} {k}") for t, d, k in zip(df[TITLE], df[DESC], df[KEYW], strict=False)}


# ------------------------------------------------------------- split logic ----


def test_holdout_split_is_partition_and_text_disjoint():
    from app.refine.prep import holdout_split

    df = _dataset()
    train, holdout, stats = holdout_split(df, COLS, LABEL, holdout_fraction=0.25, seed=1)
    assert len(train) + len(holdout) == len(df)
    assert not (_combined(train) & _combined(holdout))  # no shared text across sides
    assert len(holdout) > 0
    assert stats["train_rows"] == len(train) and stats["holdout_rows"] == len(holdout)


def test_holdout_split_represents_every_label_on_both_sides():
    from app.refine.prep import holdout_split

    df = _dataset()
    train, holdout, _ = holdout_split(df, COLS, LABEL, holdout_fraction=0.25, seed=1)
    for side in (train, holdout):
        assert set(side[LABEL]) == {"disc/A", "disc/B", "disc/C", "disc/D"}


def test_holdout_split_is_deterministic():
    from app.refine.prep import holdout_split

    df = _dataset()
    a = holdout_split(df, COLS, LABEL, holdout_fraction=0.3, seed=7)[1]
    b = holdout_split(df, COLS, LABEL, holdout_fraction=0.3, seed=7)[1]
    assert list(a[TITLE]) == list(b[TITLE])


def test_balance_report_counts_and_recommends_min_samples():
    from app.refine.prep import balance_report

    df = _dataset()
    report = balance_report(df, COLS, LABEL)
    assert report["label_support"] == {"disc/A": 8, "disc/B": 8, "disc/C": 8, "disc/D": 8}
    assert report["recommended_min_samples"] >= 1
    assert report["labels_below_recommended"] == {}


# ------------------------------------------------------------- split route ----


def _import(client, df: pd.DataFrame, name: str = "src"):
    payload = df.to_csv(sep=";", index=False).encode("utf-8")
    return client.post("/refine/datasets/import",
                       files={"file": (f"{name}.csv", payload, "text/csv")},
                       data={"name": name}, headers=HEADERS)


def test_split_route_writes_train_and_holdout(make_client, tmp_path):
    client = make_client()
    _import(client, _dataset())
    r = client.post("/refine/src/split",
                    json={"text_columns": COLS, "label_column": LABEL,
                          "holdout_fraction": 0.25, "target": "prepared"},
                    headers=HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["train_rows"] + body["holdout_rows"] == 32
    assert "balance" in body

    names = {d["name"] for d in client.get("/refine/datasets", headers=HEADERS).json()["datasets"]}
    assert {"prepared_train", "prepared_holdout"} <= names
    ops = json.loads((tmp_path / "data" / "refine" / "prepared_train.ops.json").read_text("utf-8"))
    assert ops[-1]["op"] == "holdout_split"


# -------------------------------------------------------------- push route ----


def _client_with_push(make_client, tmp_path, monkeypatch, url: str):
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({
        "api_v3": {"url": url, "api_key_env": "TEST_APIV3_KEY"},
    }), encoding="utf-8")
    monkeypatch.setenv("DATAPREP_CONFIG_FILE", str(tmp_path / "config.yaml"))
    monkeypatch.setenv("TEST_APIV3_KEY", "apiv3-secret")
    client = make_client()
    _import(client, _dataset(), "src")
    return client


def test_push_route_uploads_refine_dataset(make_client, tmp_path, monkeypatch):
    import app.apiv3 as apiv3

    client = _client_with_push(make_client, tmp_path, monkeypatch, "http://127.0.0.1:8021")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["key"] = request.headers.get("X-API-Key")
        captured["body"] = request.content
        return httpx.Response(200, json={"status": "imported"})

    monkeypatch.setattr(apiv3, "_test_transport", httpx.MockTransport(handler))
    r = client.post("/refine/src/push", headers=HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["api_v3"] == {"status": "imported"}
    assert captured["url"].endswith("/datasets/import")
    assert captured["key"] == "apiv3-secret"
    assert b"disc/A" in captured["body"]


def test_push_route_rejects_foreign_host(make_client, tmp_path, monkeypatch):
    client = _client_with_push(make_client, tmp_path, monkeypatch, "https://evil.example.org")
    r = client.post("/refine/src/push", headers=HEADERS)
    assert r.status_code == 400
    assert "not allowed" in r.json()["detail"]
