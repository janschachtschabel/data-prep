"""Refine part 5: label audit — a second opinion from api_v3 flags rows where
the model confidently disagrees with the gold label (NEVER auto-relabels)."""

from __future__ import annotations

import httpx
import pandas as pd

HEADERS = {"X-API-Key": "test-key"}

TITLE = "properties.cclom:title"
DESC = "properties.cclom:general_description"
KEYW = "properties.cclom:general_keyword"
LABEL = "properties.ccm:taxonid"
COLS = [TITLE, DESC, KEYW]


# ------------------------------------------------------------- pure logic ----


def test_label_audit_flags_confident_disagreement_only():
    from app.refine.label_audit import audit_predictions

    gold = [{"disc/A"}, {"disc/B"}, {"disc/C"}]
    predictions = [
        [{"uri": "disc/X", "confidence": 0.9}, {"uri": "disc/Y", "confidence": 0.3}],  # flag
        [{"uri": "disc/B", "confidence": 0.95}, {"uri": "disc/Q", "confidence": 0.2}],  # gold in top-k
        [{"uri": "disc/X", "confidence": 0.5}, {"uri": "disc/C", "confidence": 0.4}],   # low confidence
    ]
    flagged = audit_predictions(gold, predictions, confidence_threshold=0.8, top_k=3)
    assert [f["index"] for f in flagged] == [0]
    entry = flagged[0]
    assert entry["gold"] == ["disc/A"]
    assert entry["top_prediction"] == "disc/X"
    assert entry["top_confidence"] == 0.9
    assert entry["model_top"] == ["disc/X", "disc/Y"]


def test_label_audit_respects_top_k_window():
    from app.refine.label_audit import audit_predictions

    gold = [{"disc/A"}]
    predictions = [[
        {"uri": "disc/X", "confidence": 0.9},
        {"uri": "disc/Y", "confidence": 0.6},
        {"uri": "disc/A", "confidence": 0.4},  # gold is rank 3
    ]]
    assert audit_predictions(gold, predictions, confidence_threshold=0.8, top_k=3) == []
    flagged = audit_predictions(gold, predictions, confidence_threshold=0.8, top_k=2)
    assert [f["index"] for f in flagged] == [0]  # gold outside the top-2 window now


# ------------------------------------------------------------------ route ----


def _import(client, df: pd.DataFrame, name: str = "curated"):
    payload = df.to_csv(sep=";", index=False).encode("utf-8")
    return client.post("/refine/datasets/import",
                       files={"file": (f"{name}.csv", payload, "text/csv")},
                       data={"name": name}, headers=HEADERS)


def _predict_transport(mapping: dict[str, list[dict]]) -> httpx.MockTransport:
    """Return canned ranked predictions keyed by a substring of the text."""
    def handler(request: httpx.Request) -> httpx.Response:
        import json
        body = json.loads(request.content.decode("utf-8"))
        results = []
        for text in body["texts"]:
            preds = next((v for k, v in mapping.items() if k in text), [])
            results.append({"predictions": preds})
        return httpx.Response(200, json={"results": results})
    return httpx.MockTransport(handler)


def _configured(make_client, tmp_path, monkeypatch, transport):
    import yaml

    import app.apiv3 as apiv3

    (tmp_path / "config.yaml").write_text(yaml.safe_dump({
        "api_v3": {"url": "http://127.0.0.1:8021", "api_key_env": "TEST_APIV3_KEY"},
    }), encoding="utf-8")
    monkeypatch.setenv("DATAPREP_CONFIG_FILE", str(tmp_path / "config.yaml"))
    monkeypatch.setenv("TEST_APIV3_KEY", "apiv3-secret")
    monkeypatch.setattr(apiv3, "_test_transport", transport)
    return make_client()


def test_label_audit_route_returns_checklist(make_client, tmp_path, monkeypatch):
    transport = _predict_transport({
        "Elektro": [{"uri": "disc/OTHER", "confidence": 0.99}, {"uri": "disc/Z", "confidence": 0.1}],
        "Mathe": [{"uri": "disc/MATH", "confidence": 0.9}],
    })
    client = _configured(make_client, tmp_path, monkeypatch, transport)
    _import(client, pd.DataFrame([
        ["Elektro", "Elektrotechnik Grundlagen", "kw", "disc/ELEC"],
        ["Mathe", "Mathe Grundlagen", "kw", "disc/MATH"],
    ], columns=[TITLE, DESC, KEYW, LABEL]))

    r = client.post("/refine/curated/label-audit",
                    json={"model_name": "faecher", "text_columns": COLS, "label_column": LABEL,
                          "confidence_threshold": 0.8, "top_k": 3},
                    headers=HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["rows_audited"] == 2
    assert body["flagged_count"] == 1
    flagged = body["checklist"][0]
    assert flagged["gold"] == ["disc/ELEC"] and flagged["top_prediction"] == "disc/OTHER"


def test_label_audit_route_requires_configured_api_v3(make_client, tmp_path, monkeypatch):
    import yaml

    import app.apiv3 as apiv3

    (tmp_path / "config.yaml").write_text(yaml.safe_dump({"api_v3": {"url": ""}}), encoding="utf-8")
    monkeypatch.setenv("DATAPREP_CONFIG_FILE", str(tmp_path / "config.yaml"))
    monkeypatch.setattr(apiv3, "_test_transport", _predict_transport({}))
    client = make_client()
    _import(client, pd.DataFrame([["a", "b", "c", "disc/1"]], columns=[TITLE, DESC, KEYW, LABEL]))
    r = client.post("/refine/curated/label-audit",
                    json={"model_name": "m", "text_columns": COLS, "label_column": LABEL},
                    headers=HEADERS)
    assert r.status_code == 400
    assert "not configured" in r.json()["detail"]
