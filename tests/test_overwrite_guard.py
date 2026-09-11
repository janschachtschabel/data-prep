"""No write replaces an existing name unless the caller says so.

Every store used to overwrite silently: a target name typed twice destroyed
another dataset, and rebuilding a seed set under its own name threw away its
hand-edited and LLM-paid seeds (both reproduced before this change). api_v3
refuses an existing name with 409; data-prep now does the same, and takes
``overwrite=true`` as the explicit "yes, replace it". Working on a dataset in
place -- target equals source -- is not a collision and needs no flag.
"""

from __future__ import annotations

import json

import pandas as pd
import pytest

from tests.test_refine_enrich import DESC, KEYW, TITLE, _mock_session
from tests.test_refine_prep import COLS, LABEL, _dataset
from tests.test_seeds import _setup_inputs
from tests.test_vocab import NESTED

H = {"X-API-Key": "test-key"}
KEEP = pd.DataFrame({"a": ["important"]})


def _import(client, df: pd.DataFrame, name: str, **form):
    payload = df.to_csv(sep=";", index=False).encode("utf-8")
    return client.post("/refine/datasets/import", files={"file": (f"{name}.csv", payload, "text/csv")},
                       data={"name": name, **form}, headers=H)


def _rows(client, name: str) -> list[dict]:
    return client.get(f"/refine/{name}/rows", headers=H).json()["rows"]


@pytest.fixture
def client(make_client):
    c = make_client()
    assert _import(c, KEEP, "keep").status_code == 200
    return c


# ------------------------------------------------------------ refine layer ----


def test_import_refuses_a_taken_name_then_replaces_on_request(client):
    r = _import(client, pd.DataFrame({"a": ["other"]}), "keep")
    assert r.status_code == 409
    assert "keep" in r.json()["detail"]
    assert _rows(client, "keep") == [{"a": "important"}]

    assert _import(client, pd.DataFrame({"a": ["other"]}), "keep", overwrite="true").status_code == 200
    assert _rows(client, "keep") == [{"a": "other"}]


RULE = {"op": "rules", "params": {"rules": [{"column": "a", "op": "ne", "value": "zzz"}]}}


@pytest.mark.parametrize("route, body", [
    ("op", RULE),
    ("filter", {"filter": "length", "params": {"min_chars": 0}, "text_columns": ["a"], "label_column": "a"}),
    ("join", {"right": "src", "keys": [{"left": "a", "right": "a"}], "how": "left"}),
])
def test_a_step_refuses_to_land_on_another_dataset(client, route, body):
    assert _import(client, pd.DataFrame({"a": ["x"]}), "src").status_code == 200
    r = client.post(f"/refine/src/{route}", json={**body, "target": "keep"}, headers=H)
    assert r.status_code == 409, r.text
    assert _rows(client, "keep") == [{"a": "important"}]
    r = client.post(f"/refine/src/{route}", json={**body, "target": "keep", "overwrite": True}, headers=H)
    assert r.status_code == 200, r.text


def test_working_in_place_needs_no_flag(client):
    r = client.post("/refine/keep/op", json={**RULE, "target": "keep"}, headers=H)
    assert r.status_code == 200, r.text


def test_split_refuses_when_either_output_name_is_taken(client):
    assert _import(client, _dataset(), "src").status_code == 200
    assert _import(client, KEEP, "p_holdout").status_code == 200
    body = {"text_columns": COLS, "label_column": LABEL, "holdout_fraction": 0.25, "target": "p"}
    r = client.post("/refine/src/split", json=body, headers=H)
    assert r.status_code == 409
    names = {d["name"] for d in client.get("/refine/datasets", headers=H).json()["datasets"]}
    assert "p_train" not in names, "refused before anything was written"
    assert client.post("/refine/src/split", json={**body, "overwrite": True}, headers=H).status_code == 200


def test_enrich_refuses_a_taken_target(client, monkeypatch):
    import app.routes.refine_prep as refine_route

    src = pd.DataFrame([["Optik", "Licht", ""]], columns=[TITLE, DESC, KEYW])
    assert _import(client, src, "src").status_code == 200
    session = _mock_session({"keywords": "Optik, Licht, Physik"}, monkeypatch)
    monkeypatch.setattr(refine_route, "session_for", lambda purpose, settings, override=None: session)
    body = {"mode": "keywords", "title_column": TITLE, "description_column": DESC,
            "keyword_column": KEYW, "min_keywords": 3, "target": "keep"}
    r = client.post("/refine/src/enrich", json=body, headers=H)
    assert r.status_code == 409
    assert session.usage.calls == 0, "refused before any LLM call was paid for"
    assert client.post("/refine/src/enrich", json={**body, "overwrite": True}, headers=H).status_code == 200


def test_combine_refuses_a_taken_target(client):
    cols = ["properties.cclom:title", "properties.ccm:taxonid"]
    assert _import(client, pd.DataFrame([["T1", "u1"]], columns=cols), "a").status_code == 200
    mapping = {col: col for col in cols}
    body = {"sources": [{"name": "a", "label": "A", "mapping": mapping}], "target": "keep",
            "target_columns": cols, "text_columns": cols[:1]}
    assert client.post("/refine/combine", json=body, headers=H).status_code == 409
    assert _rows(client, "keep") == [{"a": "important"}]
    assert client.post("/refine/combine", json={**body, "overwrite": True}, headers=H).status_code == 200


# ------------------------------------------------- vocabularies, references ----


def test_vocabulary_writes_refuse_a_taken_name(client, monkeypatch):
    vocab = json.dumps(NESTED).encode()
    upload = {"file": ("v.json", vocab, "application/json")}
    assert client.post("/vocabs/import", files=upload, data={"name": "v"}, headers=H).status_code == 200

    assert client.post("/vocabs/import", files=upload, data={"name": "v"}, headers=H).status_code == 409
    assert client.post("/vocabs/manual", json={"name": "v", "text": "Alpha"}, headers=H).status_code == 409
    monkeypatch.setattr("app.routes.vocabs.fetch_json", lambda url, settings: NESTED)
    fetch = {"url": "https://vocabs.openeduhub.de/x/index.json", "name": "v"}
    assert client.post("/vocabs/fetch", json=fetch, headers=H).status_code == 409
    assert client.get("/vocabs/v", headers=H).json()["concept_count"] > 1, "still the imported one"

    r = client.post("/vocabs/manual", json={"name": "v", "text": "Alpha", "overwrite": True}, headers=H)
    assert r.status_code == 200
    assert client.post("/vocabs/import", files=upload, data={"name": "v", "overwrite": "true"},
                       headers=H).status_code == 200
    assert client.post("/vocabs/fetch", json={**fetch, "overwrite": True}, headers=H).status_code == 200


def test_reference_import_refuses_a_taken_name(make_client):
    client = make_client()
    _setup_inputs(client)  # imports the reference "physik-ref"
    before = client.get("/references/physik-ref", headers=H).json()["row_count"]
    csv = (b"properties.cclom:title;properties.cclom:general_description;"
           b"properties.cclom:general_keyword;properties.ccm:taxonid\nT;D;K;u\n")
    upload = {"file": ("r.csv", csv, "text/csv")}
    r = client.post("/references/import", files=upload, data={"name": "physik-ref"}, headers=H)
    assert r.status_code == 409
    assert client.get("/references/physik-ref", headers=H).json()["row_count"] == before
    r = client.post("/references/import", files=upload, data={"name": "physik-ref", "overwrite": "true"},
                    headers=H)
    assert r.status_code == 200


# ---------------------------------------------------------------- seed sets ----


def test_rebuilding_a_seed_set_keeps_its_edits_unless_told_otherwise(make_client):
    client = make_client()
    _setup_inputs(client)
    build = {"name": "s", "vocab": "nested"}
    assert client.post("/seeds/build", json=build, headers=H).status_code == 200
    uri = next(iter(client.get("/seeds/s", headers=H).json()["concepts"]))
    edited = [{"title": "Handarbeit", "description": "manuell gepflegt", "keywords": "k"}]
    assert client.put("/seeds/s/concepts", json={"concept_uri": uri, "seeds": edited}, headers=H).status_code == 200

    assert client.post("/seeds/build", json=build, headers=H).status_code == 409
    assert client.get("/seeds/s", headers=H).json()["concepts"][uri]["seeds"][0]["title"] == "Handarbeit"
    assert client.post("/seeds/build", json={**build, "overwrite": True}, headers=H).status_code == 200


# ------------------------------------------- a name taken while working ----
# The 409 check runs before the parse / LLM / fetch await and the write after
# it, so a name created in between was replaced without asking (review
# finding). The check is repeated at the write, with no await in between.
# Each test lets the heavy function create the name itself, mid-way: the
# interleaving is deterministic instead of a race.


def _settings():
    from app.settings import get_settings

    return get_settings()  # the instance the app under test reads


def test_an_import_refuses_a_name_taken_while_it_parsed(client, monkeypatch):
    import app.routes.tables as tables_route
    from app.refine.store import save_dataset

    real = tables_route.read_table

    def racing(*args, **kwargs):
        save_dataset(_settings(), "raced", KEEP)
        return real(*args, **kwargs)

    monkeypatch.setattr(tables_route, "read_table", racing)
    assert _import(client, pd.DataFrame({"a": ["late"]}), "raced").status_code == 409
    assert _rows(client, "raced") == [{"a": "important"}]


def test_a_step_refuses_a_target_taken_while_it_ran(client, monkeypatch):
    import app.routes.tables as tables_route
    from app.refine.store import save_dataset

    assert _import(client, pd.DataFrame({"a": ["x"]}), "src").status_code == 200
    real = tables_route.run_table_op

    def racing(*args):
        save_dataset(_settings(), "raced", KEEP)
        return real(*args)

    monkeypatch.setattr(tables_route, "run_table_op", racing)
    r = client.post("/refine/src/op", json={**RULE, "target": "raced"}, headers=H)
    assert r.status_code == 409
    assert _rows(client, "raced") == [{"a": "important"}]


def test_enrich_refuses_a_target_taken_during_its_llm_calls(client, monkeypatch):
    import app.routes.refine_prep as refine_route
    from app.refine.store import save_dataset

    src = pd.DataFrame([["Optik", "Licht", ""]], columns=[TITLE, DESC, KEYW])
    assert _import(client, src, "src").status_code == 200
    session = _mock_session({"keywords": "Optik, Licht, Physik"}, monkeypatch)
    monkeypatch.setattr(refine_route, "session_for", lambda purpose, settings, override=None: session)
    real = refine_route.enrich_dataset

    async def racing(*args, **kwargs):
        save_dataset(_settings(), "raced", KEEP)
        return await real(*args, **kwargs)

    monkeypatch.setattr(refine_route, "enrich_dataset", racing)
    r = client.post("/refine/src/enrich", headers=H, json={
        "mode": "keywords", "title_column": TITLE, "description_column": DESC,
        "keyword_column": KEYW, "min_keywords": 3, "target": "raced"})
    assert r.status_code == 409
    assert _rows(client, "raced") == [{"a": "important"}]


def test_a_vocabulary_fetch_refuses_a_name_taken_while_it_fetched(client, monkeypatch):
    def racing(url, settings):
        vocabs = settings.data_dir / "vocabs"
        vocabs.mkdir(parents=True, exist_ok=True)
        (vocabs / "v.json").write_text(json.dumps(NESTED), encoding="utf-8")
        return {"@context": {}, "id": "https://x/other", "type": "ConceptScheme",
                "title": {"de": "Andere"}, "hasTopConcept": [{"id": "https://x/other/a", "prefLabel": {"de": "A"}}]}

    monkeypatch.setattr("app.routes.vocabs.fetch_json", racing)
    fetch = {"url": "https://vocabs.openeduhub.de/x/index.json", "name": "v"}
    assert client.post("/vocabs/fetch", json=fetch, headers=H).status_code == 409
    assert client.get("/vocabs/v", headers=H).json()["concept_count"] > 1, "still the raced one"


def test_the_startup_reference_import_never_replaces_an_upload_that_landed_meanwhile(tmp_path, monkeypatch):
    """The default references are imported in a thread at startup, an upload
    under the same name runs on the event loop; whichever stored second used to
    win silently. The upload finishing first must survive the late seeder."""
    from app import reference
    from app.config import DefaultReference
    from app.settings import Settings
    from tests.test_reference import _sample_csv

    settings = Settings(auth_key=None, data_dir=tmp_path / "data")
    source = tmp_path / "curated.csv"
    source.write_bytes(_sample_csv())  # 4 rows
    upload_df, upload_meta = reference.ingest_reference(
        (b"properties.cclom:title;properties.cclom:general_description;"
         b"properties.cclom:general_keyword;properties.ccm:taxonid\nT;D;K;u\n"), name="curated")
    real = reference.ingest_reference

    def racing(raw, **kwargs):
        out = real(raw, **kwargs)
        reference.store_reference(settings, "curated", upload_df, upload_meta)  # the upload, done first
        return out

    monkeypatch.setattr(reference, "ingest_reference", racing)
    added = reference.seed_default_references(settings, [DefaultReference(name="curated", path=str(source))])
    assert added == []
    assert reference.load_reference(settings, "curated")[1]["row_count"] == 1, "the upload survives"
