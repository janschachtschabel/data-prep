"""Seed pools: distillation from references, vocab-only bootstrap (hierarchy
anchor), the store, and the editor routes. LLM calls are mocked."""

from __future__ import annotations

import json

import httpx

from app.config import Budgets, LlmEndpoint
from app.vocab import parse_vocabulary
from tests.test_vocab import NESTED

URI_NATURE = "https://example.org/vocab/nature"
URI_PHYSICS = "https://example.org/vocab/physics"
URI_OPTICS = "https://example.org/vocab/optics"
URI_ARTS = "https://example.org/vocab/arts"

CSV_HEADER = ("properties.cclom:title;properties.cclom:general_description;"
              "properties.cclom:general_keyword;properties.ccm:taxonid\n")


def _reference_csv() -> bytes:
    rows = [
        f"Linsen und Licht;Versuche zur Brechung mit Alltagsmaterial;Optik, Licht;{URI_PHYSICS}",
        f"Linsen und Licht;Versuche zur Brechung mit Alltagsmaterial;Optik, Licht;{URI_PHYSICS}",  # exact dup
        f"Hebelgesetz Quiz;Zehn Aufgaben mit Auswertung;Mechanik, Kraft;{URI_PHYSICS}",
        f"Energieformen erklärt;Video über Energieumwandlung im Alltag;Energie;{URI_PHYSICS}",
        f"Schwingungen;Simulation zum Federpendel;Schwingung, Pendel;{URI_PHYSICS}",
        f"Zellaufbau;Arbeitsblatt zur Zelle;Biologie, Zelle;{URI_NATURE}",
    ]
    return (CSV_HEADER + "\n".join(rows) + "\n").encode("utf-8")


def _ingested():
    from app.reference import ingest_reference

    return ingest_reference(_reference_csv(), name="physik-ref")


# ------------------------------------------------------------ distillation ----


def test_distill_dedupes_exactly_and_caps_per_concept():
    from app.seeds import distill_from_reference

    df, meta = _ingested()
    vocab = parse_vocabulary(NESTED)
    pools = distill_from_reference(df, meta, vocab, per_concept=3, rng_seed=42)

    physics = pools[URI_PHYSICS]
    assert len(physics) == 3  # capped (4 unique rows available)
    titles = [s["title"] for s in physics]
    assert len(set(titles)) == 3  # the exact duplicate never yields two seeds
    assert all(s["source"] == "distilled" for s in physics)
    assert pools[URI_NATURE][0]["title"] == "Zellaufbau"


def test_distill_returns_only_concepts_with_rows():
    from app.seeds import distill_from_reference

    df, meta = _ingested()
    vocab = parse_vocabulary(NESTED)
    pools = distill_from_reference(df, meta, vocab, per_concept=3)
    assert URI_OPTICS not in pools and URI_ARTS not in pools


def test_distill_is_deterministic_for_a_seed():
    from app.seeds import distill_from_reference

    df, meta = _ingested()
    vocab = parse_vocabulary(NESTED)
    a = distill_from_reference(df, meta, vocab, per_concept=2, rng_seed=7)
    b = distill_from_reference(df, meta, vocab, per_concept=2, rng_seed=7)
    assert a == b


# --------------------------------------------------------------- bootstrap ----


def test_bootstrap_prompt_anchors_concept_in_hierarchy():
    """A concept WITHOUT any reference data must be anchored by its place in
    the vocabulary: own label, broader path, scheme title — never raw URIs."""
    from app.seeds import bootstrap_prompt

    vocab = parse_vocabulary(NESTED)
    prompt = bootstrap_prompt(vocab, URI_OPTICS, n=4)

    assert "Optik" in prompt
    assert "Physik" in prompt and "Natur" in prompt  # broader-path anchor
    assert "Testschema" in prompt
    assert "BILDUNGSINHALTE" in prompt  # educational-catalog framing
    assert "Arbeitsblatt" in prompt  # mixed material types requested
    assert "example.org" not in prompt  # URIs mean nothing to the model
    assert "KEINE Personennamen" in prompt  # PII guard baked into the prompt


def _llm_transport(payload: dict) -> httpx.MockTransport:
    body = {
        "id": "c", "object": "chat.completion", "created": 1, "model": "m",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": json.dumps(payload)}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 9, "total_tokens": 14},
    }
    return httpx.MockTransport(lambda request: httpx.Response(200, json=body))


def _mock_session(payload: dict, monkeypatch):
    from app.llm import LlmSession

    monkeypatch.setenv("TEST_LLM_KEY", "k")
    return LlmSession(
        endpoint=LlmEndpoint(model="gpt-5.4-mini", api_key_env="TEST_LLM_KEY"),
        budgets=Budgets(),
        transport=_llm_transport(payload),
    )


def test_bootstrap_concept_scrubs_pii_and_marks_source(monkeypatch):
    import asyncio

    from app.seeds import bootstrap_concept

    vocab = parse_vocabulary(NESTED)
    session = _mock_session(
        {"seeds": [
            {"title": "Optik Grundlagen", "description": "Brechung erklärt, Fragen an a@b.de",
             "keywords": "Optik, Licht"},
            {"title": "Spiegel-Quiz", "description": "Zehn Fragen zu Reflexion und Spiegeln",
             "keywords": "Reflexion"},
        ]},
        monkeypatch,
    )
    seeds = asyncio.run(bootstrap_concept(session, vocab, URI_OPTICS, n=2))

    assert len(seeds) == 2
    assert all(s["source"] == "bootstrap" for s in seeds)
    assert "[email]" in seeds[0]["description"] and "a@b.de" not in seeds[0]["description"]


# ------------------------------------------------------------------- store ----


def test_seed_set_store_roundtrip(tmp_path):
    from app.seeds import delete_seed_set, list_seed_sets, load_seed_set, save_seed_set
    from app.settings import Settings

    settings = Settings(auth_key=None, data_dir=tmp_path / "data")
    payload = {"name": "s1", "vocab": "v", "reference": None, "per_concept_target": 5,
               "concepts": {URI_OPTICS: {"seeds": []}}}
    save_seed_set(settings, "s1", payload)
    assert [s["name"] for s in list_seed_sets(settings)] == ["s1"]
    assert load_seed_set(settings, "s1")["concepts"][URI_OPTICS] == {"seeds": []}
    delete_seed_set(settings, "s1")
    assert list_seed_sets(settings) == []


# ------------------------------------------------------------------ routes ----


def _setup_inputs(client) -> None:
    headers = {"X-API-Key": "test-key"}
    r = client.post("/vocabs/import",
                    files={"file": ("nested.json", json.dumps(NESTED).encode(), "application/json")},
                    data={"name": "nested"}, headers=headers)
    assert r.status_code == 200, r.text
    r = client.post("/references/import",
                    files={"file": ("physik-ref.csv", _reference_csv(), "text/csv")},
                    data={"name": "physik-ref"}, headers=headers)
    assert r.status_code == 200, r.text


def test_seed_routes_require_auth(make_client):
    client = make_client()
    assert client.get("/seeds").status_code == 401


def test_build_with_reference_distills(make_client):
    client = make_client()
    headers = {"X-API-Key": "test-key"}
    _setup_inputs(client)

    r = client.post("/seeds/build",
                    json={"name": "faecher", "vocab": "nested", "reference": "physik-ref",
                          "per_concept": 3},
                    headers=headers)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "faecher"
    assert body["concept_count"] == 5  # every vocab concept is present
    assert body["concepts_with_seeds"] == 2  # physics + nature had rows

    detail = client.get("/seeds/faecher", headers=headers).json()
    assert len(detail["concepts"][URI_PHYSICS]["seeds"]) == 3
    assert detail["concepts"][URI_OPTICS]["seeds"] == []


def test_build_vocab_only_creates_empty_pools(make_client):
    client = make_client()
    headers = {"X-API-Key": "test-key"}
    _setup_inputs(client)

    r = client.post("/seeds/build",
                    json={"name": "pure", "vocab": "nested", "per_concept": 4}, headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["concepts_with_seeds"] == 0

    listed = client.get("/seeds", headers=headers).json()["seed_sets"]
    summary = {k: listed[0][k] for k in ("name", "vocab", "concept_count", "concepts_with_seeds")}
    assert summary == {"name": "pure", "vocab": "nested", "concept_count": 5, "concepts_with_seeds": 0}


def test_bootstrap_route_fills_one_concept(make_client, monkeypatch):
    client = make_client()
    headers = {"X-API-Key": "test-key"}
    _setup_inputs(client)
    client.post("/seeds/build", json={"name": "pure", "vocab": "nested", "per_concept": 2},
                headers=headers)

    import app.routes.seeds as seeds_route

    session = _mock_session(
        {"seeds": [{"title": "Optik Video", "description": "Erklärvideo zur Lichtbrechung im Alltag",
                    "keywords": "Optik, Brechung"}]},
        monkeypatch,
    )
    monkeypatch.setattr(seeds_route, "session_for", lambda purpose, settings, override=None: session)

    r = client.post("/seeds/pure/bootstrap", json={"concept_uri": URI_OPTICS, "n": 1},
                    headers=headers)
    assert r.status_code == 200, r.text
    assert r.json()["added"] == 1

    detail = client.get("/seeds/pure", headers=headers).json()
    seeds = detail["concepts"][URI_OPTICS]["seeds"]
    assert seeds[0]["source"] == "bootstrap"


def test_bootstrap_route_threads_llm_headers_into_override(make_client, monkeypatch):
    """The X-LLM-Key / X-LLM-Model headers must reach session_for as an override,
    so a caller can drive the LLM with their OWN key on a keyless instance."""
    client = make_client()
    headers = {"X-API-Key": "test-key", "X-LLM-Key": "byo-key", "X-LLM-Model": "gpt-5.4-mini"}
    _setup_inputs(client)
    client.post("/seeds/build", json={"name": "pure", "vocab": "nested", "per_concept": 2},
                headers={"X-API-Key": "test-key"})

    import app.routes.seeds as seeds_route

    captured: dict = {}
    session = _mock_session(
        {"seeds": [{"title": "Optik Video", "description": "Erklärvideo zur Lichtbrechung im Alltag",
                    "keywords": "Optik"}]},
        monkeypatch,
    )

    def _capture(purpose, settings, override=None):
        captured["purpose"], captured["override"] = purpose, override
        return session

    monkeypatch.setattr(seeds_route, "session_for", _capture)

    r = client.post("/seeds/pure/bootstrap", json={"concept_uri": URI_OPTICS, "n": 1},
                    headers=headers)
    assert r.status_code == 200, r.text
    assert captured["purpose"] == "seeds"
    assert captured["override"].api_key == "byo-key"
    assert captured["override"].model == "gpt-5.4-mini"


def test_bootstrap_route_maps_llm_config_error_to_503(make_client, monkeypatch):
    """A missing API key surfaces as LlmConfigError — the request never reached
    the upstream, so 502 (bad gateway) is wrong. It must map to 503."""
    client = make_client()
    headers = {"X-API-Key": "test-key"}
    _setup_inputs(client)
    client.post("/seeds/build", json={"name": "pure", "vocab": "nested", "per_concept": 2},
                headers=headers)

    import app.routes.seeds as seeds_route
    from app.llm import LlmConfigError

    async def _boom(*_args, **_kwargs):
        raise LlmConfigError("Environment variable 'TEST_LLM_KEY' is not set")

    monkeypatch.setattr(seeds_route, "session_for", lambda purpose, settings, override=None: object())
    monkeypatch.setattr(seeds_route, "bootstrap_concept", _boom)

    r = client.post("/seeds/pure/bootstrap", json={"concept_uri": URI_OPTICS, "n": 1},
                    headers=headers)
    assert r.status_code == 503
    assert "not set" in r.json()["detail"]


def test_editor_put_replaces_seeds_with_manual_source(make_client):
    client = make_client()
    headers = {"X-API-Key": "test-key"}
    _setup_inputs(client)
    client.post("/seeds/build", json={"name": "pure", "vocab": "nested", "per_concept": 2},
                headers=headers)

    r = client.put("/seeds/pure/concepts",
                   json={"concept_uri": URI_ARTS,
                         "seeds": [{"title": "Farbenlehre", "description": "Grundlagen der Farbmischung",
                                    "keywords": "Farbe, Kunst"}]},
                   headers=headers)
    assert r.status_code == 200, r.text

    detail = client.get("/seeds/pure", headers=headers).json()
    seeds = detail["concepts"][URI_ARTS]["seeds"]
    assert seeds[0]["source"] == "manual"

    r = client.put("/seeds/pure/concepts",
                   json={"concept_uri": URI_ARTS, "seeds": [{"description": "ohne Titel"}]},
                   headers=headers)
    assert r.status_code == 422  # schema-invalid seed rejected


def test_unknown_seed_set_and_concept_yield_404_and_400(make_client):
    client = make_client()
    headers = {"X-API-Key": "test-key"}
    _setup_inputs(client)
    client.post("/seeds/build", json={"name": "pure", "vocab": "nested", "per_concept": 2},
                headers=headers)

    assert client.get("/seeds/missing", headers=headers).status_code == 404
    r = client.put("/seeds/pure/concepts",
                   json={"concept_uri": "https://example.org/vocab/unknown",
                         "seeds": []}, headers=headers)
    assert r.status_code == 400


def test_a_vocabulary_named_after_a_long_file_can_seed_a_set(make_client):
    """Imported without a typed name, a vocabulary is named after its file, and
    that name may exceed 100 characters -- which the seed build's `vocab` field
    refused (422) although the vocabulary itself loaded fine."""
    client = make_client()
    headers = {"X-API-Key": "test-key"}
    long_name = "wlo-vocabulary-export-" + "x" * 110
    r = client.post("/vocabs/import", headers=headers,
                    files={"file": (f"{long_name}.json", json.dumps(NESTED).encode(), "application/json")})
    assert r.status_code == 200, r.text
    assert r.json()["name"] == long_name
    r = client.post("/seeds/build", json={"name": "s", "vocab": long_name}, headers=headers)
    assert r.status_code == 200, r.text


# ------------------------------------------ edits while the LLM is working ----
# bootstrap and terms loaded the set BEFORE their LLM call and saved that copy
# after it: an edit made meanwhile was reverted, and a set deleted meanwhile
# came back (review finding). Each test lets the LLM step make the change.


class _FakeSession:
    class usage:  # noqa: N801 - mirrors LlmSession.usage
        @staticmethod
        def as_dict() -> dict:
            return {"calls": 1}


def _pure(client) -> dict:
    headers = {"X-API-Key": "test-key"}
    _setup_inputs(client)
    r = client.post("/seeds/build", json={"name": "pure", "vocab": "nested", "per_concept": 2}, headers=headers)
    assert r.status_code == 200, r.text
    return headers


def test_a_bootstrap_keeps_an_edit_made_while_the_llm_worked(make_client, monkeypatch):
    import app.routes.seeds as seeds_route
    from app.seeds import load_seed_set, save_seed_set
    from app.settings import get_settings

    client = make_client()
    headers = _pure(client)

    async def racing(session, vocab, uri, n):
        payload = load_seed_set(get_settings(), "pure")
        payload["concepts"][URI_ARTS]["seeds"] = [
            {"title": "Handarbeit", "description": "manuell", "keywords": "k", "source": "manual"}]
        save_seed_set(get_settings(), "pure", payload)  # the editor, meanwhile
        return [{"title": "Neu", "description": "vom LLM", "keywords": "k", "source": "llm"}]

    monkeypatch.setattr(seeds_route, "session_for", lambda purpose, settings, override=None: _FakeSession())
    monkeypatch.setattr(seeds_route, "bootstrap_concept", racing)
    r = client.post("/seeds/pure/bootstrap", json={"concept_uri": URI_OPTICS, "n": 1}, headers=headers)
    assert r.status_code == 200, r.text
    concepts = client.get("/seeds/pure", headers=headers).json()["concepts"]
    assert concepts[URI_ARTS]["seeds"][0]["title"] == "Handarbeit", "the edit survives"
    assert concepts[URI_OPTICS]["seeds"][-1]["title"] == "Neu", "and the new seeds land"


def test_a_bootstrap_does_not_bring_back_a_set_deleted_meanwhile(make_client, monkeypatch):
    import app.routes.seeds as seeds_route
    from app.seeds import delete_seed_set
    from app.settings import get_settings

    client = make_client()
    headers = _pure(client)

    async def racing(session, vocab, uri, n):
        delete_seed_set(get_settings(), "pure")
        return [{"title": "Neu", "description": "vom LLM", "keywords": "k", "source": "llm"}]

    monkeypatch.setattr(seeds_route, "session_for", lambda purpose, settings, override=None: _FakeSession())
    monkeypatch.setattr(seeds_route, "bootstrap_concept", racing)
    r = client.post("/seeds/pure/bootstrap", json={"concept_uri": URI_OPTICS, "n": 1}, headers=headers)
    assert r.status_code == 404
    assert client.get("/seeds/pure", headers=headers).status_code == 404


def test_refining_terms_does_not_bring_back_a_set_deleted_meanwhile(make_client, monkeypatch):
    import app.routes.seeds as seeds_route
    from app.seeds import delete_seed_set
    from app.settings import get_settings

    client = make_client()
    headers = _pure(client)

    async def racing(session, vocab, uri, terms, *, context, n):
        delete_seed_set(get_settings(), "pure")
        return ["Brechung", "Linse"]

    monkeypatch.setattr(seeds_route, "session_for", lambda purpose, settings, override=None: _FakeSession())
    monkeypatch.setattr(seeds_route, "refine_concept_terms", racing)
    r = client.post("/seeds/pure/terms", json={"concept_uri": URI_OPTICS, "n": 5}, headers=headers)
    assert r.status_code == 404
    assert client.get("/seeds/pure", headers=headers).status_code == 404
