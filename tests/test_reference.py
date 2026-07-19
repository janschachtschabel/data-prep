"""Reference sets: CSV ingest with input PII scrub, grouping, and the routes.

The hard guarantee under test: raw uploads are scrubbed IN MEMORY — plaintext
PII never reaches disk.
"""

from __future__ import annotations

import json

import pytest

CSV_HEADER = ("properties.cclom:title;properties.cclom:general_description;"
              "properties.cclom:general_keyword;properties.ccm:taxonid\n")
URI_A = "http://w3id.org/openeduhub/vocabs/discipline/460"
URI_B = "http://w3id.org/openeduhub/vocabs/discipline/380"


def _sample_csv() -> bytes:
    rows = [
        f"Optik Grundlagen;Einführung mit Versuchen, Fragen an test@example.org;Licht, Physik;{URI_A}",
        f"Mechanik Quiz;Zehn Aufgaben zur Mechanik;Kraft, Hebel, Tel. 030/123456;{URI_A}",
        f"Sauberes Arbeitsblatt;Ganz ohne Kontaktdaten;Photosynthese;{URI_A},{URI_B}",
        "Ohne Label;Beschreibung ohne Zuordnung;Sonstiges;",
    ]
    return (CSV_HEADER + "\n".join(rows) + "\n").encode("utf-8")


# ------------------------------------------------------------------ ingest ----


def test_ingest_scrubs_all_text_columns_and_groups_labels():
    from app.reference import ingest_reference

    df, meta = ingest_reference(_sample_csv(), name="physik")
    assert meta["name"] == "physik"
    assert meta["row_count"] == 4
    # Input scrub happened (mask policy): plaintext gone, masks present.
    joined = df.to_csv(sep=";", index=False)
    assert "test@example.org" not in joined and "030/123456" not in joined
    assert "[email]" in joined and "[phone]" in joined
    assert meta["pii"]["rows_affected"] == 2
    assert meta["pii"]["counts"] == {"email": 1, "phone": 1}
    # Multi-label cells count every URI; the label-less row lands nowhere.
    assert meta["group_counts"] == {URI_A: 3, URI_B: 1}


def test_list_exposes_column_mapping(make_client):
    """The reference list must show which columns feed title/description/keywords
    and the label — so the field mapping is visible in the UI, not guessed."""
    client = make_client()
    headers = {"X-API-Key": "test-key"}
    r = client.post("/references/import",
                    files={"file": ("physik.csv", _sample_csv(), "text/csv")},
                    data={"name": "physik"}, headers=headers)
    assert r.status_code == 200, r.text
    listed = client.get("/references", headers=headers).json()["references"][0]
    assert listed["text_columns"][0] == "properties.cclom:title"
    assert listed["label_column"] == "properties.ccm:taxonid"


def test_ingest_rejects_missing_columns():
    from app.reference import ingest_reference

    broken = b"title;label\nfoo;bar\n"
    with pytest.raises(ValueError, match="properties.cclom:title"):
        ingest_reference(broken, name="x")


def test_ingest_rejects_unparseable_csv():
    from app.reference import ingest_reference

    with pytest.raises(ValueError, match="CSV"):
        ingest_reference(b"\x00\xff\x00\xff", name="x")


def test_ingest_with_custom_columns():
    from app.reference import ingest_reference

    raw = b"text;fach\nEin Text mit mail@x.de;bio\n"
    df, meta = ingest_reference(raw, name="custom", text_columns=("text",), label_column="fach")
    assert meta["group_counts"] == {"bio": 1}
    assert "[email]" in df.iloc[0]["text"]


def test_load_reference_rejects_path_traversal(tmp_path):
    """load_reference builds a filesystem path from ``name``. A traversal name
    must be rejected in the loader itself — not every caller sanitizes (the
    background run at runs.py loads a persisted, unsanitized reference name)."""
    from fastapi import HTTPException

    from app.reference import load_reference
    from app.settings import Settings

    settings = Settings(auth_key=None, data_dir=tmp_path / "data")
    with pytest.raises(HTTPException) as exc:
        load_reference(settings, "../secret")
    assert exc.value.status_code == 400


# ------------------------------------------------------------------ routes ----


def _import(client, payload: bytes, name: str = "physik", extra: dict | None = None):
    return client.post(
        "/references/import",
        files={"file": (f"{name}.csv", payload, "text/csv")},
        data={"name": name, **(extra or {})},
        headers={"X-API-Key": "test-key"},
    )


def test_reference_routes_require_auth(make_client):
    client = make_client()
    assert client.get("/references").status_code == 401


def test_import_list_detail_delete_roundtrip(make_client):
    client = make_client()
    headers = {"X-API-Key": "test-key"}

    r = _import(client, _sample_csv())
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["name"] == "physik"
    assert body["row_count"] == 4
    assert body["pii"]["rows_affected"] == 2

    listed = client.get("/references", headers=headers).json()["references"]
    assert [x["name"] for x in listed] == ["physik"]
    assert listed[0]["group_count"] == 2

    detail = client.get("/references/physik", headers=headers).json()
    assert detail["group_counts"][URI_A] == 3

    assert client.delete("/references/physik", headers=headers).json() == {"deleted": "physik"}
    assert client.get("/references/physik", headers=headers).status_code == 404


def test_stored_reference_file_never_contains_plaintext_pii(make_client, tmp_path):
    client = make_client()
    assert _import(client, _sample_csv()).status_code == 200

    stored = tmp_path / "data" / "references" / "physik.csv"
    assert stored.exists()
    content = stored.read_text(encoding="utf-8")
    assert "test@example.org" not in content and "030/123456" not in content
    assert "[email]" in content

    meta = json.loads((tmp_path / "data" / "references" / "physik.meta.json").read_text("utf-8"))
    assert meta["pii"]["counts"] == {"email": 1, "phone": 1}


def test_import_rejects_bad_input(make_client):
    client = make_client()
    r = _import(client, b"title;label\nfoo;bar\n")
    assert r.status_code == 400
    assert "properties.cclom:title" in r.json()["detail"]
    r = _import(client, _sample_csv(), name="../escape")
    assert r.status_code == 400


# ------------------------------------------------ default references (config) ----


def test_seed_default_references_imports_scrubbed_and_is_idempotent(tmp_path):
    from app.config import DefaultReference
    from app.reference import references_dir, seed_default_references
    from app.settings import Settings

    source = tmp_path / "curated.csv"
    source.write_bytes(_sample_csv())  # contains an email + phone
    settings = Settings(auth_key=None, data_dir=tmp_path / "data")

    added = seed_default_references(settings, [DefaultReference(name="curated-30k", path=str(source))])
    assert added == ["curated-30k"]

    stored = references_dir(settings) / "curated-30k.csv"
    assert stored.exists()
    content = stored.read_text(encoding="utf-8")
    assert "test@example.org" not in content and "[email]" in content  # scrubbed on import

    # Second run is a no-op (already present).
    assert seed_default_references(settings, [DefaultReference(name="curated-30k", path=str(source))]) == []


def test_seed_default_references_skips_missing_source(tmp_path):
    from app.config import DefaultReference
    from app.reference import seed_default_references
    from app.settings import Settings

    settings = Settings(auth_key=None, data_dir=tmp_path / "data")
    added = seed_default_references(
        settings, [DefaultReference(name="nope", path=str(tmp_path / "does-not-exist.csv"))]
    )
    assert added == []  # missing source is skipped, not fatal


def test_config_parses_references_defaults(tmp_path):
    from app.config import load_config

    path = tmp_path / "config.yaml"
    path.write_text(
        "references:\n  defaults:\n    - name: curated-30k\n      path: ../api_v3/data/data_30k.csv\n",
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.references.defaults[0].name == "curated-30k"
    assert cfg.references.defaults[0].path.endswith("data_30k.csv")
