"""Refine part 6: additive LLM enrichment (fills gaps only, marks
enriched_fields, never overwrites) for keywords and descriptions."""

from __future__ import annotations

import asyncio
import json

import httpx
import pandas as pd

from app.config import Budgets, LlmEndpoint

HEADERS = {"X-API-Key": "test-key"}

TITLE = "properties.cclom:title"
DESC = "properties.cclom:general_description"
KEYW = "properties.cclom:general_keyword"
LABEL = "properties.ccm:taxonid"


async def _fake_keywords(prompt, schema):
    return schema(keywords="Optik, Licht, kontakt@x.de")  # includes PII to prove scrubbing


async def _fake_description(prompt, schema):
    return schema(description="Eine sachliche Beschreibung des Materials zum Thema.")


# ------------------------------------------------------------- pure logic ----


def test_enrich_keywords_fills_gaps_marks_and_scrubs():
    from app.refine.enrich import enrich_dataset

    df = pd.DataFrame(
        [["Optik", "Licht und Brechung", ""],           # empty keywords -> enriched
         ["Mechanik", "Kraft", "Kraft, Hebel, Physik"]],  # already has >= min -> untouched
        columns=[TITLE, DESC, KEYW],
    )
    new, stats = asyncio.run(enrich_dataset(
        df, title_col=TITLE, description_col=DESC, keyword_col=KEYW,
        mode="keywords", min_keywords=3, complete=_fake_keywords,
    ))
    assert stats["enriched"] == 1
    assert new.iloc[0][KEYW] == "Optik, Licht, [email]"  # PII scrubbed
    assert new.iloc[0]["enriched_fields"] == "keywords"
    assert new.iloc[1][KEYW] == "Kraft, Hebel, Physik"   # untouched
    assert new.iloc[1]["enriched_fields"] == ""


def test_enrich_description_only_fills_empty():
    from app.refine.enrich import enrich_dataset

    df = pd.DataFrame(
        [["Optik", "", "kw"], ["Mechanik", "Vorhandene Beschreibung", "kw"]],
        columns=[TITLE, DESC, KEYW],
    )
    new, stats = asyncio.run(enrich_dataset(
        df, title_col=TITLE, description_col=DESC, keyword_col=KEYW,
        mode="description", min_keywords=3, complete=_fake_description,
    ))
    assert stats["enriched"] == 1
    assert new.iloc[0][DESC] == "Eine sachliche Beschreibung des Materials zum Thema."
    assert new.iloc[1][DESC] == "Vorhandene Beschreibung"  # not overwritten


# ------------------------------------------------------------------ route ----


def _import(client, df: pd.DataFrame, name: str = "curated"):
    payload = df.to_csv(sep=";", index=False).encode("utf-8")
    return client.post("/refine/datasets/import",
                       files={"file": (f"{name}.csv", payload, "text/csv")},
                       data={"name": name}, headers=HEADERS)


def _mock_session(payload: dict, monkeypatch):
    from app.llm import LlmSession

    monkeypatch.setenv("TEST_LLM_KEY", "k")
    body = {
        "id": "c", "object": "chat.completion", "created": 1, "model": "m",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": json.dumps(payload)}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 9, "total_tokens": 14},
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    return LlmSession(endpoint=LlmEndpoint(model="gpt-5.4-nano", api_key_env="TEST_LLM_KEY"),
                      budgets=Budgets(), transport=transport)


def test_enrich_route_writes_target_with_marks(make_client, tmp_path, monkeypatch):
    import app.routes.refine as refine_route

    client = make_client()
    _import(client, pd.DataFrame([["Optik", "Licht und Brechung", ""]],
                                 columns=[TITLE, DESC, KEYW, LABEL][:3]))
    session = _mock_session({"keywords": "Optik, Licht, Physik"}, monkeypatch)
    monkeypatch.setattr(refine_route, "session_for", lambda purpose, settings, override=None: session)

    r = client.post("/refine/curated/enrich",
                    json={"mode": "keywords", "title_column": TITLE, "description_column": DESC,
                          "keyword_column": KEYW, "min_keywords": 3, "target": "enriched"},
                    headers=HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["enriched"] == 1

    names = {d["name"] for d in client.get("/refine/datasets", headers=HEADERS).json()["datasets"]}
    assert "enriched" in names
    ops = json.loads((tmp_path / "data" / "refine" / "enriched.ops.json").read_text("utf-8"))
    assert ops[-1]["op"] == "enrich" and ops[-1]["mode"] == "keywords"


def test_enrich_route_rejects_unknown_mode(make_client, tmp_path, monkeypatch):
    import app.routes.refine as refine_route

    client = make_client()
    _import(client, pd.DataFrame([["Optik", "Licht", "kw"]], columns=[TITLE, DESC, KEYW]))
    monkeypatch.setattr(refine_route, "session_for",
                        lambda purpose, settings, override=None: _mock_session({"keywords": "x"}, monkeypatch))
    r = client.post("/refine/curated/enrich",
                    json={"mode": "nonsense", "title_column": TITLE, "description_column": DESC,
                          "keyword_column": KEYW, "target": "out"},
                    headers=HEADERS)
    assert r.status_code == 422  # Literal schema rejects it


def test_enrich_route_maps_llm_config_error_to_503(make_client, monkeypatch):
    """A missing API key is LlmConfigError: the upstream was never reached, so
    502 is the wrong answer. The seed routes already map it to 503; enrich
    answered 502 for the same condition (audit 2026-09-11, API1)."""
    import app.routes.refine as refine_route
    from app.llm import LlmConfigError

    client = make_client()
    _import(client, pd.DataFrame([["Optik", "Licht", ""]], columns=[TITLE, DESC, KEYW]))
    monkeypatch.setattr(refine_route, "session_for",
                        lambda purpose, settings, override=None: _mock_session({"keywords": "x"}, monkeypatch))

    async def _boom(*_args, **_kwargs):
        raise LlmConfigError("Environment variable 'TEST_LLM_KEY' is not set")

    monkeypatch.setattr(refine_route, "enrich_dataset", _boom)
    r = client.post("/refine/curated/enrich",
                    json={"mode": "keywords", "title_column": TITLE, "description_column": DESC,
                          "keyword_column": KEYW, "target": "out"},
                    headers=HEADERS)
    assert r.status_code == 503
    assert "not set" in r.json()["detail"]
