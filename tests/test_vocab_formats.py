"""Alternative vocabulary inputs: SkoHub Turtle (.ttl) and manual concept lists.

Both must converge on the SkoHub JSON-LD dict that ``parse_vocabulary`` already
understands, so every assertion below round-trips through that one parser — the
same path the store validates and persists.
"""

from __future__ import annotations

import pytest

# A tiny educational-context scheme in SkoHub-style Turtle. Deliberately mixes:
# a comment line, comma-separated object lists, a full-IRI predicate (whose '#'
# must NOT be eaten as a comment), CURIE + IRI subjects, and a broader/narrower
# hierarchy that must nest correctly.
TTL = """\
@prefix skos: <http://www.w3.org/2004/02/skos/core#> .
@prefix dct: <http://purl.org/dc/terms/> .

# A tiny scheme for the parser test.
<http://w3id.org/openeduhub/vocabs/educationalContext/> a skos:ConceptScheme ;
    dct:title "Bildungsstufe"@de, "Educational Context"@en ;
    skos:hasTopConcept <http://w3id.org/openeduhub/vocabs/educationalContext/schule>,
        <http://w3id.org/openeduhub/vocabs/educationalContext/hochschule> .

<http://w3id.org/openeduhub/vocabs/educationalContext/schule> a skos:Concept ;
    skos:prefLabel "Schule"@de, "school"@en ;
    skos:altLabel "Schulunterricht"@de ;
    skos:narrower <http://w3id.org/openeduhub/vocabs/educationalContext/grundschule> ;
    skos:topConceptOf <http://w3id.org/openeduhub/vocabs/educationalContext/> .

<http://w3id.org/openeduhub/vocabs/educationalContext/grundschule>
    <http://www.w3.org/2004/02/skos/core#prefLabel> "Primarstufe"@de ;
    skos:altLabel "Grundschule"@de ;
    skos:broader <http://w3id.org/openeduhub/vocabs/educationalContext/schule> .

<http://w3id.org/openeduhub/vocabs/educationalContext/hochschule> a skos:Concept ;
    skos:prefLabel "Hochschule"@de, "higher education"@en .
"""


# ------------------------------------------------------------------- turtle ----


def test_turtle_parses_scheme_title_and_concepts():
    from app.vocab import parse_vocabulary
    from app.vocab_turtle import turtle_to_jsonld

    vocab = parse_vocabulary(turtle_to_jsonld(TTL))
    assert vocab.title["de"] == "Bildungsstufe"
    assert vocab.title["en"] == "Educational Context"
    # Three concepts; grundschule nests under schule, so only two are top.
    assert len(vocab.concepts) == 3
    assert vocab.top == [
        "http://w3id.org/openeduhub/vocabs/educationalContext/schule",
        "http://w3id.org/openeduhub/vocabs/educationalContext/hochschule",
    ]


def test_turtle_keeps_labels_alt_labels_and_languages():
    from app.vocab import parse_vocabulary
    from app.vocab_turtle import turtle_to_jsonld

    vocab = parse_vocabulary(turtle_to_jsonld(TTL))
    schule = vocab.concepts["http://w3id.org/openeduhub/vocabs/educationalContext/schule"]
    assert schule.pref_label == {"de": "Schule", "en": "school"}
    assert schule.alt_labels["de"] == ["Schulunterricht"]
    # Full-IRI predicate (<...#prefLabel>) must be understood too.
    grund = vocab.concepts["http://w3id.org/openeduhub/vocabs/educationalContext/grundschule"]
    assert grund.pref_label["de"] == "Primarstufe"
    assert "Grundschule" in grund.alt_labels["de"]


def test_turtle_builds_hierarchy_from_broader_and_narrower():
    from app.vocab import parse_vocabulary
    from app.vocab_turtle import turtle_to_jsonld

    vocab = parse_vocabulary(turtle_to_jsonld(TTL))
    schule = "http://w3id.org/openeduhub/vocabs/educationalContext/schule"
    grund = "http://w3id.org/openeduhub/vocabs/educationalContext/grundschule"
    assert vocab.concepts[grund].broader == schule
    assert grund in vocab.concepts[schule].narrower
    # grundschule is reached once, via nesting — not duplicated as a top concept.
    assert vocab.subtree(schule) == [schule, grund]


def test_turtle_rejects_input_without_concepts():
    from app.vocab_turtle import turtle_to_jsonld

    with pytest.raises(ValueError):
        turtle_to_jsonld("@prefix skos: <http://www.w3.org/2004/02/skos/core#> .\n")


def test_turtle_rejects_unterminated_iri():
    from app.vocab_turtle import turtle_to_jsonld

    with pytest.raises(ValueError):
        turtle_to_jsonld("<http://example.org/broken a skos:Concept .")


# ------------------------------------------------------------------- manual ----


def test_manual_mints_uris_for_bare_labels():
    from app.vocab import parse_vocabulary
    from app.vocab_formats import manual_to_jsonld

    raw = manual_to_jsonld("Mathematik\nDeutsch\n", title="My subjects",
                           lang="de", base_uri="urn:dataprep:my-subjects")
    vocab = parse_vocabulary(raw)
    assert len(vocab.concepts) == 2
    labels = {vocab.label(u) for u in vocab.concepts}
    assert labels == {"Mathematik", "Deutsch"}
    # Minted URIs live under the given base and are stable/slugged.
    assert all(u.startswith("urn:dataprep:my-subjects/") for u in vocab.concepts)


def test_manual_accepts_explicit_uris_in_either_order():
    from app.vocab import parse_vocabulary
    from app.vocab_formats import manual_to_jsonld

    raw = manual_to_jsonld(
        "Deutsch | http://w3id.org/openeduhub/vocabs/discipline/120\n"
        "http://w3id.org/openeduhub/vocabs/discipline/380 | Biologie\n",
        title="Fächer", lang="de", base_uri="urn:x",
    )
    vocab = parse_vocabulary(raw)
    assert vocab.label("http://w3id.org/openeduhub/vocabs/discipline/120") == "Deutsch"
    assert vocab.label("http://w3id.org/openeduhub/vocabs/discipline/380") == "Biologie"


def test_manual_ignores_blank_and_comment_lines_and_dedupes_uris():
    from app.vocab import parse_vocabulary
    from app.vocab_formats import manual_to_jsonld

    raw = manual_to_jsonld("# heading\n\nMathe\nMathe\n  \n", title="t",
                           lang="de", base_uri="urn:x")
    vocab = parse_vocabulary(raw)
    # Two rows with the same label keep two distinct concepts (unique URIs).
    assert len(vocab.concepts) == 2


def test_manual_rejects_empty_input():
    from app.vocab_formats import manual_to_jsonld

    with pytest.raises(ValueError):
        manual_to_jsonld("\n  \n# only a comment\n", title="t", lang="de", base_uri="urn:x")


# ------------------------------------------------------------------- routes ----


def test_import_route_accepts_turtle_file(make_client):
    client = make_client()
    headers = {"X-API-Key": "test-key"}
    r = client.post(
        "/vocabs/import",
        files={"file": ("edu.ttl", TTL.encode("utf-8"), "text/turtle")},
        data={"name": "edu-ttl", "label_field": "properties.ccm:educationalcontext"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["concept_count"] == 3
    assert r.json()["label_field"] == "properties.ccm:educationalcontext"
    assert client.get("/vocabs/edu-ttl", headers=headers).json()["concept_count"] == 3


def test_import_route_rejects_unknown_extension(make_client):
    client = make_client()
    r = client.post(
        "/vocabs/import",
        files={"file": ("thing.xml", b"<rdf/>", "application/xml")},
        headers={"X-API-Key": "test-key"},
    )
    assert r.status_code == 400


def test_manual_route_creates_vocabulary(make_client):
    client = make_client()
    headers = {"X-API-Key": "test-key"}
    r = client.post(
        "/vocabs/manual",
        json={"name": "handmade", "text": "Mathematik\nDeutsch\nSachkunde",
              "lang": "de", "label_field": "properties.ccm:taxonid"},
        headers=headers,
    )
    assert r.status_code == 200, r.text
    assert r.json()["name"] == "handmade"
    assert r.json()["concept_count"] == 3
    assert r.json()["label_field"] == "properties.ccm:taxonid"
    detail = client.get("/vocabs/handmade", headers=headers).json()
    assert {n["label"] for n in detail["tree"]} == {"Mathematik", "Deutsch", "Sachkunde"}


def test_manual_route_rejects_empty_text(make_client):
    client = make_client()
    r = client.post(
        "/vocabs/manual",
        json={"name": "empty", "text": "   \n# nothing\n"},
        headers={"X-API-Key": "test-key"},
    )
    assert r.status_code == 400
