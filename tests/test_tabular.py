"""Reading and writing tables in the formats operators actually have.

The refine store keeps ONE shape internally (semicolon CSV, UTF-8, all
strings), so formats are a concern of the edges. These tests pin the edges:
what comes out of a nested JSON export, and how a format is recognised when
the filename is unhelpful or lies.
"""

from __future__ import annotations

import gzip
import json

import pytest

from app.tabular import flatten_record, sniff_format

# One row shaped like a WLO edu-sharing export: every property is a LIST even
# when it holds a single value, which is exactly the nesting that makes such an
# export unusable as a table without flattening.
WLO_ROW = {
    "ref": {"id": "abc-123"},
    "properties": {
        "cclom:title": ["Bruchrechnen"],
        "cclom:general_keyword": ["Mathematik", "Bruch"],
        "ccm:taxonid": ["http://w3id.org/openeduhub/vocabs/discipline/380"],
    },
    "collections": [{"id": "c1"}, {"id": "c2"}],
    "score": 0.75,
    "public": True,
    "deleted": None,
}


class TestFlattenRecord:
    def test_nested_objects_become_dot_paths(self):
        flat = flatten_record(WLO_ROW)
        assert flat["properties.cclom:title"] == "Bruchrechnen"
        assert flat["ref.id"] == "abc-123"

    def test_a_list_of_scalars_is_joined(self):
        flat = flatten_record(WLO_ROW)
        assert flat["properties.cclom:general_keyword"] == "Mathematik,Bruch"

    def test_the_list_separator_is_configurable(self):
        flat = flatten_record(WLO_ROW, list_separator="|")
        assert flat["properties.cclom:general_keyword"] == "Mathematik|Bruch"

    def test_a_single_element_list_loses_its_brackets(self):
        """The WLO case: one value wrapped in a list is a plain cell, not '[x]'."""
        flat = flatten_record(WLO_ROW)
        assert flat["properties.ccm:taxonid"] == (
            "http://w3id.org/openeduhub/vocabs/discipline/380"
        )

    def test_a_list_of_objects_is_kept_as_json_text(self):
        """A documented limitation: there is no sensible column for it, so the
        value survives as text instead of being silently dropped."""
        flat = flatten_record(WLO_ROW)
        assert json.loads(flat["collections"]) == [{"id": "c1"}, {"id": "c2"}]

    def test_scalars_are_stringified_json_style(self):
        flat = flatten_record(WLO_ROW)
        assert flat["score"] == "0.75"
        assert flat["public"] == "true"  # not Python's "True" — this came from JSON

    def test_null_becomes_the_empty_cell(self):
        assert flatten_record(WLO_ROW)["deleted"] == ""

    def test_an_empty_list_becomes_the_empty_cell(self):
        assert flatten_record({"tags": []})["tags"] == ""

    def test_an_empty_object_becomes_the_empty_cell(self):
        """Nothing to descend into, so it cannot produce a column of its own."""
        assert flatten_record({"meta": {}})["meta"] == ""

    def test_nesting_deeper_than_two_levels_still_resolves(self):
        flat = flatten_record({"a": {"b": {"c": "deep"}}})
        assert flat["a.b.c"] == "deep"

    def test_every_value_is_a_string(self):
        """The store is all-strings; a stray int would break the CSV round-trip."""
        flat = flatten_record(WLO_ROW)
        assert all(isinstance(v, str) for v in flat.values())


class TestSniffFormat:
    @pytest.mark.parametrize(
        ("filename", "expected"),
        [
            ("data.csv", "csv"),
            ("data.CSV", "csv"),
            ("data.csv.gz", "csv.gz"),
            ("data.json", "json"),
            ("data.jsonl", "jsonl"),
            ("data.ndjson", "jsonl"),
            ("data.jsonl.gz", "jsonl.gz"),
        ],
    )
    def test_the_extension_decides_when_it_is_known(self, filename, expected):
        assert sniff_format(filename, b"irrelevant") == expected

    def test_gzip_content_overrides_a_filename_that_lies(self):
        """Compression must be read from the bytes: a mislabelled .csv that is
        really gzipped would otherwise be parsed as text and fail confusingly."""
        raw = gzip.compress(b"a;b\n1;2\n")
        assert sniff_format("data.csv", raw) == "csv.gz"

    def test_content_decides_when_the_extension_is_unknown(self):
        assert sniff_format("export.dat", b'{"a": 1}\n{"a": 2}\n') == "jsonl"
        assert sniff_format("export.dat", b'[{"a": 1}]') == "json"
        assert sniff_format("export.dat", b"a;b\n1;2\n") == "csv"

    def test_a_gzipped_payload_without_a_useful_name_is_sniffed_inside(self):
        raw = gzip.compress(b'{"a": 1}\n{"a": 2}\n')
        assert sniff_format("export.gz", raw) == "jsonl.gz"

    def test_leading_whitespace_does_not_hide_json(self):
        assert sniff_format("export.dat", b'\n  [{"a": 1}]') == "json"

    def test_empty_content_falls_back_to_csv(self):
        """No evidence either way — CSV is the format this app stores, and the
        reader will raise a readable error anyway."""
        assert sniff_format("export.dat", b"") == "csv"
