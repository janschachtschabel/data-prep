"""SKOS vocabulary parsing, guarded URL fetch, and the /vocabs routes.

Real-world fixtures come from vocabs.openeduhub.de (both currently FLAT lists);
NESTED below pins the hierarchy behaviour the SkoHub JSON-LD @context allows
(`narrower` as a set of embedded concepts).
"""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import pytest

FIXTURES = Path(__file__).parent / "fixtures"
EDU_CONTEXT = json.loads((FIXTURES / "educationalContext.json").read_text(encoding="utf-8"))
DISCIPLINE = json.loads((FIXTURES / "discipline.json").read_text(encoding="utf-8"))

NESTED = {
    "id": "https://example.org/vocab/",
    "type": "ConceptScheme",
    "title": {"de": "Testschema"},
    "hasTopConcept": [
        {
            "id": "https://example.org/vocab/nature",
            "prefLabel": {"de": "Natur", "en": "Nature"},
            "narrower": [
                {
                    "id": "https://example.org/vocab/physics",
                    "prefLabel": {"de": "Physik"},
                    "altLabel": {"de": ["Physiklehre"]},
                    "narrower": [
                        {"id": "https://example.org/vocab/optics", "prefLabel": {"de": "Optik"}},
                    ],
                },
                {"id": "https://example.org/vocab/biology", "prefLabel": {"de": "Biologie"}},
            ],
        },
        {"id": "https://example.org/vocab/arts", "prefLabel": {"en": "Arts"}},
    ],
}


# ---------------------------------------------------------------- parser ----


def test_parse_real_educational_context():
    from app.vocab import parse_vocabulary

    vocab = parse_vocabulary(EDU_CONTEXT)
    assert vocab.title["de"] == "Bildungsstufe"
    assert len(vocab.concepts) == 12
    assert len(vocab.top) == 12  # this vocabulary is flat
    uri = "http://w3id.org/openeduhub/vocabs/educationalContext/grundschule"
    concept = vocab.concepts[uri]
    assert concept.pref_label["de"] == "Primarstufe"
    assert "Grundschule" in concept.alt_labels["de"]
    assert concept.broader is None


def test_parse_real_discipline():
    from app.vocab import parse_vocabulary

    vocab = parse_vocabulary(DISCIPLINE)
    assert len(vocab.concepts) == 70
    uri = "http://w3id.org/openeduhub/vocabs/discipline/04013"
    assert vocab.concepts[uri].pref_label["de"] == "Wirtschaft und Verwaltung"


def test_parse_nested_hierarchy():
    from app.vocab import parse_vocabulary

    vocab = parse_vocabulary(NESTED)
    assert len(vocab.concepts) == 5
    assert vocab.top == ["https://example.org/vocab/nature", "https://example.org/vocab/arts"]
    optics = vocab.concepts["https://example.org/vocab/optics"]
    assert optics.broader == "https://example.org/vocab/physics"
    nature = vocab.concepts["https://example.org/vocab/nature"]
    assert set(nature.narrower) == {
        "https://example.org/vocab/physics",
        "https://example.org/vocab/biology",
    }


def test_subtree_returns_root_plus_descendants_in_tree_order():
    from app.vocab import parse_vocabulary

    vocab = parse_vocabulary(NESTED)
    assert vocab.subtree("https://example.org/vocab/nature") == [
        "https://example.org/vocab/nature",
        "https://example.org/vocab/physics",
        "https://example.org/vocab/optics",
        "https://example.org/vocab/biology",
    ]
    with pytest.raises(ValueError, match="Unknown concept"):
        vocab.subtree("https://example.org/vocab/nope")


def test_tree_lists_depths_in_dfs_order():
    from app.vocab import parse_vocabulary

    vocab = parse_vocabulary(NESTED)
    depths = {uri.rsplit("/", 1)[-1]: depth for uri, depth in vocab.tree()}
    assert depths == {"nature": 0, "physics": 1, "optics": 2, "biology": 1, "arts": 0}


def test_label_prefers_german_then_english():
    from app.vocab import parse_vocabulary

    vocab = parse_vocabulary(NESTED)
    assert vocab.label("https://example.org/vocab/physics") == "Physik"
    assert vocab.label("https://example.org/vocab/arts") == "Arts"  # no German label


@pytest.mark.parametrize(
    "raw",
    [
        {},  # no hasTopConcept at all
        {"hasTopConcept": "not-a-list"},
        {"hasTopConcept": [{"prefLabel": {"de": "ohne id"}}]},
    ],
)
def test_parse_rejects_malformed_input(raw):
    from app.vocab import parse_vocabulary

    with pytest.raises(ValueError, match="SKOS"):
        parse_vocabulary(raw)


# ------------------------------------------------------------ URL guards ----


def _settings(**overrides):
    from app.settings import Settings

    return Settings(auth_key=None, **overrides)


def _transport(payload: bytes, status: int = 200, headers: dict | None = None) -> httpx.MockTransport:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(status, content=payload, headers=headers or {})

    return httpx.MockTransport(handler)


def test_fetch_rejects_plain_http():
    from app.fetch import FetchError, fetch_json

    with pytest.raises(FetchError, match="https"):
        fetch_json("http://vocabs.openeduhub.de/x.json", _settings())


def test_fetch_rejects_hosts_outside_allowlist():
    from app.fetch import FetchError, fetch_json

    with pytest.raises(FetchError, match="not allowed"):
        fetch_json("https://evil.example.org/x.json", _settings())


def test_fetch_rejects_oversized_bodies():
    from app.fetch import FetchError, fetch_json

    settings = _settings(fetch_allowed_hosts="example.org", fetch_max_mb=1)
    body = b"x" * (1024 * 1024 + 1)
    with pytest.raises(FetchError, match="exceeds"):
        fetch_json("https://example.org/big.json", settings, transport=_transport(body))


def test_fetch_rejects_redirects():
    from app.fetch import FetchError, fetch_json

    settings = _settings(fetch_allowed_hosts="example.org")
    transport = _transport(b"", status=302, headers={"location": "https://elsewhere.org/"})
    with pytest.raises(FetchError, match="edirect"):
        fetch_json("https://example.org/moved.json", settings, transport=transport)


def test_fetch_rejects_non_json_bodies():
    from app.fetch import FetchError, fetch_json

    settings = _settings(fetch_allowed_hosts="example.org")
    with pytest.raises(FetchError, match="JSON"):
        fetch_json("https://example.org/x.json", settings, transport=_transport(b"<html>nope</html>"))


def test_fetch_returns_parsed_json_for_allowed_host():
    from app.fetch import fetch_json

    settings = _settings()  # default allowlist: vocabs.openeduhub.de
    payload = json.dumps(NESTED).encode()
    url = "https://vocabs.openeduhub.de/w3id.org/openeduhub/vocabs/x/index.json"
    assert fetch_json(url, settings, transport=_transport(payload)) == NESTED


# ---------------------------------------------------------------- routes ----


def _upload(client, payload: dict, name: str | None = None, filename: str = "vocab.json"):
    data = {"name": name} if name else {}
    return client.post(
        "/vocabs/import",
        files={"file": (filename, json.dumps(payload).encode(), "application/json")},
        data=data,
        headers={"X-API-Key": "test-key"},
    )


def test_vocab_routes_require_auth(make_client):
    client = make_client()
    assert client.get("/vocabs").status_code == 401


def test_import_list_detail_delete_roundtrip(make_client):
    client = make_client()
    headers = {"X-API-Key": "test-key"}

    r = _upload(client, EDU_CONTEXT, name="educationalContext")
    assert r.status_code == 200, r.text
    assert r.json() == {"name": "educationalContext", "title": "Bildungsstufe",
                        "concept_count": 12, "label_field": ""}

    listed = client.get("/vocabs", headers=headers).json()["vocabularies"]
    assert [v["name"] for v in listed] == ["educationalContext"]

    detail = client.get("/vocabs/educationalContext", headers=headers).json()
    assert detail["concept_count"] == 12
    assert len(detail["tree"]) == 12
    assert {"de", "en"} <= set(detail["languages"])
    first = detail["tree"][0]
    assert set(first) == {"uri", "label", "depth"}

    assert client.delete("/vocabs/educationalContext", headers=headers).json() == {
        "deleted": "educationalContext"
    }
    assert client.get("/vocabs/educationalContext", headers=headers).status_code == 404


def test_import_derives_name_from_filename(make_client):
    client = make_client()
    r = _upload(client, NESTED, filename="mySchema.json")
    assert r.status_code == 200
    assert r.json()["name"] == "mySchema"


def test_import_rejects_invalid_payloads(make_client):
    client = make_client()
    r = client.post(
        "/vocabs/import",
        files={"file": ("x.json", b"not json at all", "application/json")},
        headers={"X-API-Key": "test-key"},
    )
    assert r.status_code == 400
    r = _upload(client, {"hasTopConcept": "broken"})
    assert r.status_code == 400
    r = _upload(client, NESTED, name="../escape")
    assert r.status_code == 400


def test_fetch_route_stores_vocabulary(make_client, monkeypatch):
    client = make_client()
    headers = {"X-API-Key": "test-key"}
    url = "https://vocabs.openeduhub.de/w3id.org/openeduhub/vocabs/educationalContext/index.json"

    import app.routes.vocabs as vocabs_route

    monkeypatch.setattr(vocabs_route, "fetch_json", lambda u, s: EDU_CONTEXT)
    r = client.post("/vocabs/fetch", json={"url": url}, headers=headers)
    assert r.status_code == 200, r.text
    # Name derived from the URL path segment before index.json.
    assert r.json()["name"] == "educationalContext"

    listed = client.get("/vocabs", headers=headers).json()["vocabularies"]
    assert listed[0]["concept_count"] == 12


def test_fetch_route_reports_guard_errors_as_400(make_client):
    client = make_client()
    r = client.post(
        "/vocabs/fetch",
        json={"url": "https://evil.example.org/x.json"},
        headers={"X-API-Key": "test-key"},
    )
    assert r.status_code == 400
    assert "not allowed" in r.json()["detail"]


def test_import_stores_and_returns_optional_label_field(make_client):
    client = make_client()
    headers = {"X-API-Key": "test-key"}
    r = _upload(client, EDU_CONTEXT, name="edu")
    # No field given -> empty (optional).
    assert r.status_code == 200 and r.json()["label_field"] == ""

    r = client.post(
        "/vocabs/import",
        files={"file": ("d.json", json.dumps(DISCIPLINE).encode(), "application/json")},
        data={"name": "disc", "label_field": "properties.ccm:taxonid"},
        headers=headers,
    )
    assert r.status_code == 200 and r.json()["label_field"] == "properties.ccm:taxonid"

    listed = {v["name"]: v for v in client.get("/vocabs", headers=headers).json()["vocabularies"]}
    assert listed["disc"]["label_field"] == "properties.ccm:taxonid"
    assert listed["edu"]["label_field"] == ""
    assert client.get("/vocabs/disc", headers=headers).json()["label_field"] == "properties.ccm:taxonid"

    # Deleting a vocabulary also removes its field sidecar.
    client.delete("/vocabs/disc", headers=headers)
    assert client.get("/vocabs/disc", headers=headers).status_code == 404


def test_fetch_route_stores_label_field(make_client, monkeypatch):
    client = make_client()
    headers = {"X-API-Key": "test-key"}
    import app.routes.vocabs as vocabs_route

    monkeypatch.setattr(vocabs_route, "fetch_json", lambda u, s: EDU_CONTEXT)
    r = client.post(
        "/vocabs/fetch",
        json={"url": "https://vocabs.openeduhub.de/w3id.org/openeduhub/vocabs/educationalContext/index.json",
              "label_field": "properties.ccm:educationalcontext"},
        headers=headers,
    )
    assert r.status_code == 200
    assert r.json()["label_field"] == "properties.ccm:educationalcontext"
