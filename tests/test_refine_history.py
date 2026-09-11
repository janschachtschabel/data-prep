"""A dataset's operation history describes THAT dataset, whatever name it reuses.

preview_or_apply already carries the source's history and writes the target's
in one go. Four other writers did not: import left the previous dataset's
history in place, and split, enrich and combine APPENDED to whatever history
the target name already had. Reusing a name therefore produced a history that
described a different table -- the provenance the history exists for, wrong.
"""

from __future__ import annotations

import pandas as pd

from tests.test_refine_enrich import DESC, KEYW, TITLE, _mock_session
from tests.test_refine_prep import COLS, LABEL, _dataset

HEADERS = {"X-API-Key": "test-key"}


def _import(client, df: pd.DataFrame, name: str):
    payload = df.to_csv(sep=";", index=False).encode("utf-8")
    r = client.post("/refine/datasets/import", files={"file": (f"{name}.csv", payload, "text/csv")},
                    data={"name": name, "overwrite": "true"}, headers=HEADERS)
    assert r.status_code == 200, r.text


def _history(client, name: str) -> list[str]:
    ops = client.get(f"/refine/{name}/ops", headers=HEADERS).json()["ops"]
    return [o.get("filter") or o.get("op") for o in ops]


def _with_unrelated_history(client, name: str) -> None:
    """A dataset under ``name`` whose history is one select_columns step --
    a step no source in these tests has, so a leaked history is visible."""
    _import(client, pd.DataFrame({"a": ["1"]}), name)
    r = client.post(f"/refine/{name}/op", headers=HEADERS,
                    json={"op": "select_columns", "params": {"keep": ["a"]}, "target": name})
    assert r.status_code == 200, r.text
    assert _history(client, name) == ["select_columns"]


def test_a_reimport_starts_a_fresh_history(make_client):
    client = make_client()
    _with_unrelated_history(client, "d")
    _import(client, pd.DataFrame({"a": ["new"]}), "d")
    assert _history(client, "d") == []


def test_split_carries_the_source_history_not_the_targets(make_client):
    client = make_client()
    _import(client, _dataset(), "src")
    _with_unrelated_history(client, "p_train")
    r = client.post("/refine/src/split", headers=HEADERS,
                    json={"text_columns": COLS, "label_column": LABEL, "holdout_fraction": 0.25,
                          "target": "p", "overwrite": True})
    assert r.status_code == 200, r.text
    assert _history(client, "p_train") == ["holdout_split"]
    assert _history(client, "p_holdout") == ["holdout_split"]


def test_enrich_carries_the_source_history_not_the_targets(make_client, monkeypatch):
    import app.routes.refine as refine_route

    client = make_client()
    _import(client, pd.DataFrame([["Optik", "Licht und Brechung", ""]], columns=[TITLE, DESC, KEYW]), "src")
    client.post("/refine/src/op", headers=HEADERS, json={
        "op": "rules", "target": "src",
        "params": {"rules": [{"column": TITLE, "op": "ne", "value": "zzz"}]}})
    _with_unrelated_history(client, "out")
    session = _mock_session({"keywords": "Optik, Licht, Physik"}, monkeypatch)
    monkeypatch.setattr(refine_route, "session_for", lambda purpose, settings, override=None: session)
    r = client.post("/refine/src/enrich", headers=HEADERS, json={
        "mode": "keywords", "title_column": TITLE, "description_column": DESC,
        "keyword_column": KEYW, "min_keywords": 3, "target": "out", "overwrite": True})
    assert r.status_code == 200, r.text
    assert _history(client, "out") == ["rules", "enrich"]


def test_combine_starts_a_fresh_history(make_client):
    """A combination is a new table made from several: no single source's
    history describes it, and certainly not the history the name had before."""
    client = make_client()
    cols = ["properties.cclom:title", "properties.ccm:taxonid"]
    _import(client, pd.DataFrame([["T1", "u1"]], columns=cols), "a")
    _import(client, pd.DataFrame([["T2", "u2"]], columns=cols), "b")
    _with_unrelated_history(client, "t")
    mapping = {col: col for col in cols}
    r = client.post("/refine/combine", headers=HEADERS, json={
        "sources": [{"name": "a", "label": "A", "mapping": mapping},
                    {"name": "b", "label": "B", "mapping": mapping}],
        "target": "t", "target_columns": cols, "text_columns": cols[:1], "overwrite": True})
    assert r.status_code == 200, r.text
    assert _history(client, "t") == ["combine"]
