"""Loading and saving a table must not stall the event loop.

The app runs one worker. While a route read or wrote the 81 MB WLO export on the
event loop, /health and run polling waited up to 2.4 s (import), 1.0 s (a load)
and 0.8 s (applying a step). These tests spy on the three primitives every load
and save ends in -- ``pandas.read_csv``, ``DataFrame.to_csv``, ``os.replace`` --
and collect each call that ran on the loop's own thread.

Moving the writes off the loop must not cost what running on it gave for free:
the check before a write and the write itself stay one step, so a name another
request took meanwhile is still refused.
"""

from __future__ import annotations

import asyncio
import json
import os
import time
import traceback
from concurrent.futures import ThreadPoolExecutor

import httpx
import pandas as pd
import pytest

import app
from app.config import Budgets, LlmEndpoint

HEADERS = {"X-API-Key": "test-key"}
TITLE = "properties.cclom:title"
DESC = "properties.cclom:general_description"
KEYW = "properties.cclom:general_keyword"
LABEL = "properties.ccm:taxonid"
ID = "id"

APP_DIR = os.path.dirname(app.__file__)


def _frame() -> pd.DataFrame:
    return pd.DataFrame(
        [[str(i), f"Titel {i}", f"Ein Text über das Thema Nummer {i}.", "Optik, Licht",
          "Physik" if i % 2 else "Chemie"] for i in range(12)],
        columns=[ID, TITLE, DESC, KEYW, LABEL])


def _import(client, name: str, df: pd.DataFrame | None = None) -> None:
    payload = (_frame() if df is None else df).to_csv(sep=";", index=False).encode("utf-8")
    r = client.post("/refine/datasets/import", headers=HEADERS, data={"name": name},
                    files={"file": (f"{name}.csv", payload, "text/csv")})
    assert r.status_code == 200, r.text


def _on_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


def _caller() -> str:
    """The nearest app frame, so a failure names the line that blocked."""
    for frame in reversed(traceback.extract_stack()[:-2]):
        if frame.filename.startswith(APP_DIR):
            return f"{os.path.relpath(frame.filename, APP_DIR)}:{frame.lineno}"
    return "?"


@pytest.fixture
def on_loop(monkeypatch) -> list[str]:
    """Every read_csv / to_csv / os.replace that ran ON the event loop's thread."""
    seen: list[str] = []

    def spy(name, real):
        def wrapper(*args, **kwargs):
            if _on_loop():
                seen.append(f"{name} from {_caller()}")
            return real(*args, **kwargs)
        return wrapper

    monkeypatch.setattr(pd, "read_csv", spy("read_csv", pd.read_csv))
    monkeypatch.setattr(pd.DataFrame, "to_csv", spy("to_csv", pd.DataFrame.to_csv))
    monkeypatch.setattr(os, "replace", spy("os.replace", os.replace))
    return seen


def _ok(response) -> None:
    assert response.status_code == 200, f"{response.request.url}: {response.text}"


def test_the_table_routes_load_and_save_off_the_event_loop(make_client, on_loop):
    client = make_client()
    _import(client, "quelle")
    _import(client, "rechts")

    for response in [
        client.get("/refine/datasets", headers=HEADERS),
        client.get("/refine/quelle/rows?limit=5", headers=HEADERS),
        client.get("/refine/quelle/profile", headers=HEADERS),
        client.get(f"/refine/quelle/duplicates?keys={ID}", headers=HEADERS),
        client.get("/refine/quelle/download", headers=HEADERS),
        client.get("/refine/quelle/ops", headers=HEADERS),
        client.post("/refine/quelle/op", headers=HEADERS, json={
            "op": "select_columns", "params": {"keep": [ID, TITLE, LABEL]}, "target": "schmal"}),
        client.post("/refine/quelle/join", headers=HEADERS, json={
            "right": "rechts", "keys": [{"left": ID, "right": ID}], "target": "verbunden"}),
        client.delete("/refine/schmal", headers=HEADERS),
    ]:
        _ok(response)

    assert on_loop == []


def test_the_label_routes_load_and_save_off_the_event_loop(make_client, on_loop, monkeypatch):
    import app.routes.refine_prep as prep

    async def pushed(settings, csv_text, filename):
        return {"accepted": filename}

    async def predicted(settings, texts, model_name, top_k=3):
        return [[{"uri": "Physik", "confidence": 0.9}] for _ in texts]

    monkeypatch.setattr(prep, "push_csv", pushed)
    monkeypatch.setattr(prep, "predict_batch", predicted)
    client = make_client()
    _import(client, "quelle")
    _import(client, "zweite")
    mapping = {c: c for c in (TITLE, DESC, KEYW, LABEL)}

    for response in [
        client.post("/refine/quelle/analyze", headers=HEADERS, json={}),
        client.post("/refine/quelle/preflight", headers=HEADERS, json={}),
        client.post("/refine/quelle/filter", headers=HEADERS, json={
            "filter": "dedupe_exact", "target": "gefiltert"}),
        client.post("/refine/quelle/split", headers=HEADERS, json={
            "target": "teil", "holdout_fraction": 0.3}),
        client.post("/refine/combine/suggest", headers=HEADERS, json={
            "sources": ["quelle", "zweite"]}),
        client.post("/refine/combine", headers=HEADERS, json={
            "target": "zusammen", "sources": [
                {"name": "quelle", "label": "q", "mapping": mapping},
                {"name": "zweite", "label": "z", "mapping": mapping}]}),
        client.post("/refine/quelle/label-audit", headers=HEADERS, json={"model_name": "m"}),
        client.post("/refine/quelle/push", headers=HEADERS),
    ]:
        _ok(response)

    assert on_loop == []


def _session(content: dict, monkeypatch):
    from app.llm import LlmSession

    monkeypatch.setenv("TEST_LLM_KEY", "k")
    body = {
        "id": "c", "object": "chat.completion", "created": 1, "model": "m",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": json.dumps(content)}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 9, "total_tokens": 14},
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    return LlmSession(endpoint=LlmEndpoint(model="gpt-5.4-nano", api_key_env="TEST_LLM_KEY"),
                      budgets=Budgets(), transport=transport)


def test_the_llm_routes_load_and_save_off_the_event_loop(make_client, on_loop, monkeypatch):
    import app.routes.refine_balance as balance
    import app.routes.refine_prep as prep

    gaps = _frame().assign(**{KEYW: ""})
    enrich_session = _session({"values": ["Optik", "Licht", "Physik"]}, monkeypatch)
    balance_session = _session({"items": [{"values": [
        f"Neuer Titel {i}", f"Ein neuer Text über ein anderes Thema {i}."]}
        for i in range(10)]}, monkeypatch)
    monkeypatch.setattr(prep, "session_for", lambda purpose, settings, override=None: enrich_session)
    monkeypatch.setattr(balance, "session_for",
                        lambda purpose, settings, override=None: balance_session)
    client = make_client()
    _import(client, "quelle", gaps)
    fields = [{"column": TITLE}, {"column": DESC}]

    for response in [
        client.post("/refine/quelle/enrich", headers=HEADERS, json={
            "fields": [*fields, {"column": KEYW, "separator": ",", "min_values": 3}],
            "target_field": KEYW, "target": "angereichert", "limit": 2}),
        client.post("/refine/quelle/balance", headers=HEADERS, json={
            "fields": fields, "label_column": LABEL, "target_per_label": 8,
            "target": "ausgeglichen"}),
    ]:
        _ok(response)

    assert on_loop == []


def test_a_name_taken_while_a_step_ran_is_still_refused(make_client, monkeypatch):
    """Two steps race for one new name. Before the write, each checks that the name
    is free; the write takes a while. The second must see the first one's table,
    not the moment before it landed -- or it replaces it without being asked."""
    client = make_client()
    _import(client, "a")
    _import(client, "b")
    real = pd.DataFrame.to_csv

    def slow(self, path_or_buf=None, *args, **kwargs):
        if path_or_buf is not None:
            time.sleep(0.3)  # writing a large table takes a while
        return real(self, path_or_buf, *args, **kwargs)

    monkeypatch.setattr(pd.DataFrame, "to_csv", slow)
    body = {"op": "select_columns", "params": {"keep": [TITLE]}, "target": "ziel"}

    with ThreadPoolExecutor(max_workers=2) as pool:
        sent = {source: pool.submit(client.post, f"/refine/{source}/op", json=body,
                                    headers=HEADERS)
                for source in ("a", "b")}
        codes = {source: future.result().status_code for source, future in sent.items()}

    assert sorted(codes.values()) == [200, 409], codes
    winner = next(source for source, code in codes.items() if code == 200)
    ops = client.get("/refine/ziel/ops", headers=HEADERS).json()["ops"]
    assert ops[-1]["source"] == winner
