"""The balance route: preview without paying, generate into a NEW dataset.

The engine is covered in tests/test_refine_balance.py; what matters here is the
HTTP contract — which failures map to which status, and that a refused or failing
run never touches the source dataset.
"""

from __future__ import annotations

import json

import httpx
import pandas as pd

from app.config import Budgets, LlmEndpoint

HEADERS = {"X-API-Key": "test-key"}

TITLE = "properties.cclom:title"
DESC = "properties.cclom:general_description"
KEYW = "properties.cclom:general_keyword"
LABEL = "properties.ccm:taxonid"

FIELDS = [
    {"column": TITLE, "guidance": "Ein kurzer Titel."},
    {"column": DESC, "guidance": "Zwei Sätze zum Inhalt."},
    {"column": KEYW, "separator": ",", "min_values": 3, "guidance": "3-6 Schlagwörter."},
]


def _unbalanced() -> pd.DataFrame:
    rows = [[f"Mathe {i}", f"Ein Text über Zahlen {i}.", "a, b, c", "Mathematik"]
            for i in range(4)]
    rows.append(["Optik", "Ein Text über Licht.", "a, b, c", "Physik"])
    return pd.DataFrame(rows, columns=[TITLE, DESC, KEYW, LABEL])


def _import(client, df: pd.DataFrame, name: str = "quelle"):
    payload = df.to_csv(sep=";", index=False).encode("utf-8")
    return client.post("/refine/datasets/import",
                       files={"file": (f"{name}.csv", payload, "text/csv")},
                       data={"name": name}, headers=HEADERS)


def _mock_session(items: list[list[str]], monkeypatch):
    from app.llm import LlmSession

    monkeypatch.setenv("TEST_LLM_KEY", "k")
    content = json.dumps({"items": [{"values": item} for item in items]})
    body = {
        "id": "c", "object": "chat.completion", "created": 1, "model": "m",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": content}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 9, "total_tokens": 14},
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    return LlmSession(endpoint=LlmEndpoint(model="gpt-5.4-nano", api_key_env="TEST_LLM_KEY"),
                      budgets=Budgets(), transport=transport)


def _use(session, monkeypatch):
    import app.routes.refine_balance as route

    monkeypatch.setattr(route, "session_for",
                        lambda purpose, settings, override=None: session)


def test_a_preview_costs_nothing_and_says_what_the_run_would_do(make_client, monkeypatch):
    """The preview is the whole point of asking before generating: it must answer
    without an LLM configured at all, which is also why it cannot be a side effect of
    the run itself."""
    import app.routes.refine_balance as route

    client = make_client()
    _import(client, _unbalanced())

    def refuse(*_args, **_kwargs):
        raise AssertionError("a preview must not open an LLM session")

    monkeypatch.setattr(route, "session_for", refuse)

    r = client.post("/refine/quelle/balance", headers=HEADERS, json={
        "fields": FIELDS, "label_column": LABEL, "target_per_label": 4, "dry_run": True})

    assert r.status_code == 200, r.text
    plan = r.json()
    assert plan["rows_to_add"] == 3
    assert plan["per_label"]["Physik"] == {
        "support": 1, "deficit": 3, "batches": 1, "synthetic_share": 0.75}


def test_the_run_writes_a_new_dataset_and_leaves_the_source_alone(make_client, monkeypatch):
    """Every refine operation writes a NEW dataset; the source a user spent an hour
    curating is never edited in place."""
    client = make_client()
    _import(client, _unbalanced())
    _use(_mock_session([["Akustik", "Ein Text über Schall.", "a, b, c"],
                        ["Thermo", "Ein Text über Wärme.", "d, e, f"],
                        ["Statik", "Ein Text über Kräfte.", "g, h, i"]], monkeypatch),
         monkeypatch)

    r = client.post("/refine/quelle/balance", headers=HEADERS, json={
        "fields": FIELDS, "label_column": LABEL, "target_per_label": 4,
        "target": "ausgeglichen"})

    assert r.status_code == 200, r.text
    assert r.json()["rows_added"] == 3
    assert r.json()["target"] == "ausgeglichen"

    names = {d["name"] for d in client.get("/refine/datasets", headers=HEADERS).json()["datasets"]}
    assert {"quelle", "ausgeglichen"} <= names
    source = client.get("/refine/quelle/rows?limit=50", headers=HEADERS).json()
    assert len(source["rows"]) == 5, "the source kept its five rows"


def test_an_unknown_column_is_refused_before_anything_is_paid_for(make_client, monkeypatch):
    import app.routes.refine_balance as route

    client = make_client()
    _import(client, _unbalanced())
    monkeypatch.setattr(route, "session_for",
                        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("paid too early")))

    r = client.post("/refine/quelle/balance", headers=HEADERS, json={
        "fields": [{"column": "gibtsnicht"}], "label_column": LABEL,
        "target_per_label": 4, "target": "out"})

    assert r.status_code == 400
    assert "gibtsnicht" in r.json()["detail"]


def test_an_unknown_label_column_is_refused_too(make_client, monkeypatch):
    """The label column is not one of the fields, so it needs its own check — without
    it the failure would surface as a KeyError deep in the engine."""
    import app.routes.refine_balance as route

    client = make_client()
    _import(client, _unbalanced())
    monkeypatch.setattr(route, "session_for",
                        lambda *_a, **_k: (_ for _ in ()).throw(AssertionError("paid too early")))

    r = client.post("/refine/quelle/balance", headers=HEADERS, json={
        "fields": FIELDS, "label_column": "kein_label", "target_per_label": 4,
        "target": "out"})

    assert r.status_code == 400
    assert "kein_label" in r.json()["detail"]


def test_a_foreign_provenance_column_is_a_bad_request_not_a_crash(make_client, monkeypatch):
    """The engine refuses; the route has to turn that into an answer the user can act
    on rather than a 500."""
    client = make_client()
    df = _unbalanced()
    df["generated_for"] = "Notiz"
    _import(client, df)
    _use(_mock_session([["A", "B", "c, d, e"]], monkeypatch), monkeypatch)

    r = client.post("/refine/quelle/balance", headers=HEADERS, json={
        "fields": FIELDS, "label_column": LABEL, "target_per_label": 4, "target": "out"})

    assert r.status_code == 400
    assert "generated_for" in r.json()["detail"]


def test_a_missing_llm_key_is_503_like_every_other_llm_route(make_client, monkeypatch):
    import app.routes.refine_balance as route
    from app.llm import LlmConfigError

    client = make_client()
    _import(client, _unbalanced())
    _use(_mock_session([["A", "B", "c, d, e"]], monkeypatch), monkeypatch)

    async def _boom(*_args, **_kwargs):
        raise LlmConfigError("Environment variable 'TEST_LLM_KEY' is not set")

    monkeypatch.setattr(route, "balance_dataset", _boom)

    r = client.post("/refine/quelle/balance", headers=HEADERS, json={
        "fields": FIELDS, "label_column": LABEL, "target_per_label": 4, "target": "out"})

    assert r.status_code == 503
    assert "not set" in r.json()["detail"]


def test_the_run_is_recorded_in_the_target_history(make_client, tmp_path, monkeypatch):
    """Provenance of the dataset itself: what was done, from what, and how much."""
    client = make_client()
    _import(client, _unbalanced())
    _use(_mock_session([["Akustik", "Ein Text über Schall.", "a, b, c"]], monkeypatch),
         monkeypatch)

    r = client.post("/refine/quelle/balance", headers=HEADERS, json={
        "fields": FIELDS, "label_column": LABEL, "target_per_label": 2, "target": "out"})
    assert r.status_code == 200, r.text

    ops = json.loads((tmp_path / "data" / "refine" / "out.ops.json").read_text("utf-8"))
    assert ops[-1]["op"] == "balance"
    assert ops[-1]["source"] == "quelle"
    assert ops[-1]["rows_added"] == 1


def test_balance_needs_the_api_key(make_client):
    client = make_client()
    r = client.post("/refine/quelle/balance", json={
        "fields": FIELDS, "label_column": LABEL, "target_per_label": 4, "dry_run": True})
    assert r.status_code == 401


def test_a_field_the_domain_refuses_is_a_validation_error_not_a_crash(make_client):
    """An empty separator and a single value asked for several are refused by
    `TextField`; at the HTTP boundary that has to be a 422 naming the field, not a
    500 from deep inside the engine (review #6, #10)."""
    client = make_client()
    _import(client, _unbalanced())

    for bad in ({"column": KEYW, "separator": ""}, {"column": DESC, "min_values": 2}):
        r = client.post("/refine/quelle/balance", headers=HEADERS, json={
            "fields": [{"column": TITLE}, bad], "label_column": LABEL,
            "target_per_label": 4, "dry_run": True})
        assert r.status_code == 422, (bad, r.status_code, r.text)
