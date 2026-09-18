"""Loading and saving a table must not stall the event loop.

The app runs one worker. While a route read or wrote the 81 MB WLO export on the
event loop, /health and run polling waited up to 2.4 s (import), 1.0 s (a load)
and 0.8 s (applying a step). An audit hook collects every file access below the
store's directory that runs on the loop's own thread -- tables, the history and
shape sidecars, renames, deletes, listings -- and a spy catches a whole table
serialised there without any file (the push).

Moving the writes off the loop must not cost what running on it gave for free:
the check before a write and the write itself stay one step, so a name another
request took meanwhile is still refused.
"""

from __future__ import annotations

import asyncio
import json
import os
import sys
import time
import traceback
from collections.abc import Iterator
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


# File access as the interpreter reports it: every open, rename, delete and
# listing, whichever function asked. Spies on single functions saw only the ones
# someone thought of -- reading a history, listing, deleting slipped past them
# (review of e2d0204, finding 3). A check that only stats a path (exists) raises
# no event and is allowed on the loop.
_FILE_EVENTS = {"open", "os.remove", "os.rename", "os.scandir", "os.listdir"}
_watching: dict = {}  # set by the on_loop fixture: {"root": ..., "seen": [...]}


def _audit(event: str, args: tuple) -> None:
    if not _watching or event not in _FILE_EVENTS:
        return
    for arg in args[:2] if event == "os.rename" else args[:1]:
        if not isinstance(arg, (str, os.PathLike)):
            continue  # an open file descriptor
        path = os.fspath(arg)
        if not isinstance(path, str):
            continue
        path = os.path.normcase(os.path.abspath(path))
        root = _watching["root"]
        if (path == root or path.startswith(root + os.sep)) and _on_loop():
            _watching["seen"].append(f"{event} {os.path.basename(path)} from {_caller()}")
            return


sys.addaudithook(_audit)  # permanent for the process; inert unless a test watches


@pytest.fixture
def on_loop(monkeypatch, tmp_path) -> Iterator[list[str]]:
    """Every store file access, and every whole-table serialisation, that ran ON the
    event loop's thread."""
    seen: list[str] = []
    real = pd.DataFrame.to_csv

    def to_csv(*args, **kwargs):
        if _on_loop():  # serialising a table costs as much as saving it, file or not
            seen.append(f"to_csv from {_caller()}")
        return real(*args, **kwargs)

    monkeypatch.setattr(pd.DataFrame, "to_csv", to_csv)
    _watching.update(root=os.path.normcase(os.path.abspath(tmp_path / "data" / "refine")),
                     seen=seen)
    yield seen
    _watching.clear()


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


# ------------------------------------------ one table, one history ----


def _settings():
    from app.settings import get_settings

    return get_settings()  # the instance the app under test reads


def _queue_a_write_while_reading(monkeypatch, name: str) -> None:
    """While a request reads table ``name`` on the store's thread, queue an in-place
    write of that table. The store runs one step at a time, so the write lands
    right after the step that read the table -- deterministically, no timing."""
    from app.refine import store

    real = pd.read_csv
    armed = [True]

    def reading(path, *args, **kwargs):
        df = real(path, *args, **kwargs)
        if armed[0] and str(path).endswith(f"{os.sep}{name}.csv"):
            armed[0] = False
            store._store_thread.submit(
                store.commit, _settings(), {name: (df, [{"op": "injected"}])})
        return df

    monkeypatch.setattr(pd, "read_csv", reading)


@pytest.mark.parametrize("route", ["split", "enrich", "balance"])
def test_a_result_records_the_history_of_the_table_it_was_made_from(
        make_client, monkeypatch, route):
    """Read in two store steps, the rows of one version were paired with the history
    of the next: a write queued between the two landed in the result's provenance
    (review of e2d0204, finding 1)."""
    import app.routes.refine_balance as balance
    import app.routes.refine_prep as prep

    enrich_session = _session({"values": ["Optik", "Licht", "Physik"]}, monkeypatch)
    balance_session = _session({"items": [{"values": [
        f"Neuer Titel {i}", f"Ein neuer Text über ein anderes Thema {i}."]}
        for i in range(10)]}, monkeypatch)
    monkeypatch.setattr(prep, "session_for", lambda purpose, settings, override=None: enrich_session)
    monkeypatch.setattr(balance, "session_for",
                        lambda purpose, settings, override=None: balance_session)
    client = make_client()
    _import(client, "quelle", _frame().assign(**{KEYW: ""}))
    fields = [{"column": TITLE}, {"column": DESC}]
    url, body, result = {
        "split": ("/refine/quelle/split", {"target": "teil", "holdout_fraction": 0.3}, "teil_train"),
        "enrich": ("/refine/quelle/enrich", {
            "fields": [*fields, {"column": KEYW, "separator": ",", "min_values": 3}],
            "target_field": KEYW, "target": "angereichert", "limit": 2}, "angereichert"),
        "balance": ("/refine/quelle/balance", {
            "fields": fields, "label_column": LABEL, "target_per_label": 8,
            "target": "ausgeglichen"}, "ausgeglichen"),
    }[route]
    _queue_a_write_while_reading(monkeypatch, "quelle")

    _ok(client.post(url, headers=HEADERS, json=body))

    history = [op.get("op") for op in
               client.get(f"/refine/{result}/ops", headers=HEADERS).json()["ops"]]
    assert "injected" not in history, history


def test_the_history_answer_describes_one_state_of_the_store(make_client, monkeypatch):
    """/ops checked on the loop that the table exists and read its history in a later
    store step: a delete queued in between was answered with 200 and an empty
    history for a table that had one (review of e2d0204, finding 1)."""
    import pathlib

    from app.refine import store

    client = make_client()
    _import(client, "quelle")
    _ok(client.post("/refine/quelle/op", headers=HEADERS, json={
        "op": "select_columns", "params": {"keep": [ID, TITLE]}, "target": "quelle"}))
    real = pathlib.Path.exists
    armed = [True]

    def exists(self, *args, **kwargs):
        found = real(self, *args, **kwargs)
        if armed[0] and self.name == "quelle.csv":
            armed[0] = False
            store._store_thread.submit(store.delete_dataset, _settings(), "quelle")
        return found

    monkeypatch.setattr(pathlib.Path, "exists", exists)

    r = client.get("/refine/quelle/ops", headers=HEADERS)

    assert r.status_code == 200, r.text
    assert [op.get("filter") for op in r.json()["ops"]] == ["select_columns"]
