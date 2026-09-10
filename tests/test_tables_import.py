"""The import endpoint, before and after it learned more formats.

The first class is a REGRESSION pin, written before `store.read_csv` was
swapped for `tabular.read_table`: it fixes what a semicolon upload has always
done, so the swap has to prove it changed nothing. The rest is the new
behaviour.
"""

from __future__ import annotations

import gzip
import json

import pytest

HEADERS = {"X-API-Key": "test-key"}

CSV_SEMI = b"id;title;keywords\n1;Bruchrechnen;Mathematik,Bruch\n2;Photosynthese;Biologie\n"


def _import(client, payload: bytes, filename: str = "src.csv", name: str = "src", **form):
    return client.post(
        "/refine/datasets/import",
        files={"file": (filename, payload, "application/octet-stream")},
        data={"name": name, **form},
        headers=HEADERS,
    )


class TestSemicolonImportIsUnchanged:
    """Pinned before the reader swap. Any diff here is a regression, not a feature."""

    def test_it_reports_rows_and_columns(self, make_client):
        r = _import(make_client(), CSV_SEMI)
        assert r.status_code == 200
        assert r.json() == {
            "name": "src", "rows": 2,
            "columns": ["id", "title", "keywords"],
        }

    def test_the_name_falls_back_to_the_filename_stem(self, make_client):
        client = make_client()
        r = client.post(
            "/refine/datasets/import",
            files={"file": ("Mein Datensatz.csv", CSV_SEMI, "text/csv")},
            data={}, headers=HEADERS,
        )
        assert r.status_code == 200
        # safe_name keeps case and spaces; only unsafe characters are stripped.
        assert r.json()["name"] == "Mein Datensatz"

    def test_an_unreadable_file_is_a_400_not_a_500(self, make_client):
        """The contract is the status and a client-safe message, not its wording:
        A4 changed an empty upload from a parse-failure message to an accurate
        'no data rows' one, which is an improvement rather than a regression."""
        r = _import(make_client(), b"", filename="empty.csv")
        assert r.status_code == 400
        assert r.json()["detail"]
        assert "Traceback" not in r.json()["detail"]

    def test_it_needs_a_key(self, make_client):
        client = make_client()
        r = client.post(
            "/refine/datasets/import",
            files={"file": ("src.csv", CSV_SEMI, "text/csv")}, data={},
        )
        assert r.status_code == 401


class TestNewFormats:
    def test_a_comma_separated_csv_is_accepted_when_declared(self, make_client):
        payload = b"id,title\n1,Bruchrechnen\n"
        r = _import(make_client(), payload, separator=",")
        assert r.status_code == 200
        assert r.json()["columns"] == ["id", "title"]

    def test_a_comma_csv_without_the_separator_lands_as_one_column(self, make_client):
        """Not a silent success: the operator sees one merged column and can
        re-import with the right separator."""
        r = _import(make_client(), b"id,title\n1,Bruchrechnen\n")
        assert r.json()["columns"] == ["id,title"]

    def test_gzipped_csv_is_detected_from_the_bytes(self, make_client):
        r = _import(make_client(), gzip.compress(CSV_SEMI), filename="src.csv.gz")
        assert r.status_code == 200
        assert r.json()["rows"] == 2

    def test_nested_json_becomes_dot_path_columns(self, make_client):
        payload = json.dumps([
            {"properties": {"cclom:title": ["Bruchrechnen"]}},
            {"properties": {"cclom:title": ["Photosynthese"]}},
        ]).encode("utf-8")
        r = _import(make_client(), payload, filename="export.json")
        assert r.status_code == 200
        assert r.json()["columns"] == ["properties.cclom:title"]

    def test_jsonl_is_accepted(self, make_client):
        payload = b'{"a": "1"}\n{"a": "2"}\n'
        r = _import(make_client(), payload, filename="export.jsonl")
        assert r.json()["rows"] == 2

    def test_a_declared_format_overrides_the_filename(self, make_client):
        """An operator who knows better than the extension must be able to say so."""
        r = _import(make_client(), b'{"a": "1"}\n', filename="mislabelled.csv", format="jsonl")
        assert r.status_code == 200
        assert r.json()["columns"] == ["a"]

    def test_an_unsupported_format_is_a_400_naming_what_is_supported(self, make_client):
        r = _import(make_client(), CSV_SEMI, format="parquet")
        assert r.status_code == 400
        assert "parquet" in r.json()["detail"]

    def test_a_latin1_csv_is_readable_when_declared(self, make_client):
        payload = "id;title\n1;Grün\n".encode("latin-1")
        r = _import(make_client(), payload, encoding="latin-1")
        assert r.status_code == 200

    def test_broken_json_is_a_400_naming_the_line(self, make_client):
        r = _import(make_client(), b'{"a": 1}\n{oops}\n', filename="x.jsonl")
        assert r.status_code == 400
        assert "line 2" in r.json()["detail"]


class TestEmptyCellsSurviveTheStore:
    """The all-strings promise has to hold across save and load, or a rule like
    'column is empty' means something different depending on when it runs."""

    @pytest.mark.parametrize("blank_column", ["b"])
    def test_an_empty_cell_reads_back_as_empty_text(self, make_client, blank_column):
        client = make_client()
        _import(client, b"a;b\n1;\n2;x\n")
        r = client.post(
            "/refine/src/analyze",
            json={"text_columns": ["a"], "label_column": blank_column},
            headers=HEADERS,
        )
        assert r.status_code == 200

    def test_the_literal_string_NA_is_not_a_missing_value(self, make_client):
        client = make_client()
        r = _import(client, b"a;b\nNA;1\n")
        assert r.status_code == 200
        rows = client.get("/refine/datasets", headers=HEADERS).json()["datasets"]
        assert [d for d in rows if d["name"] == "src"][0]["rows"] == 1
