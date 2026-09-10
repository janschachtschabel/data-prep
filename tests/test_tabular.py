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

from app.tabular import flatten_record, read_table, sniff_format, write_table

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


# The same two rows, expressed five ways. Every reader must land on the same
# frame -- that equivalence is the whole point of having one internal shape.
ROWS = [
    {"id": "1", "title": "Bruchrechnen", "keywords": "Mathematik,Bruch"},
    {"id": "2", "title": "Photosynthese", "keywords": "Biologie"},
]
CSV_BYTES = b"id;title;keywords\n1;Bruchrechnen;Mathematik,Bruch\n2;Photosynthese;Biologie\n"
JSON_BYTES = json.dumps(ROWS, ensure_ascii=False).encode("utf-8")
JSONL_BYTES = b"\n".join(json.dumps(r, ensure_ascii=False).encode("utf-8") for r in ROWS)


class TestReadTable:
    def _assert_is_the_expected_frame(self, df):
        assert list(df.columns) == ["id", "title", "keywords"]
        assert df.to_dict("records") == ROWS

    def test_all_five_formats_produce_the_same_frame(self):
        for raw, fmt in [
            (CSV_BYTES, "csv"),
            (gzip.compress(CSV_BYTES), "csv.gz"),
            (JSON_BYTES, "json"),
            (JSONL_BYTES, "jsonl"),
            (gzip.compress(JSONL_BYTES), "jsonl.gz"),
        ]:
            self._assert_is_the_expected_frame(read_table(raw, fmt=fmt))

    def test_auto_detection_needs_no_declared_format(self):
        self._assert_is_the_expected_frame(read_table(JSONL_BYTES, filename="x.jsonl"))
        self._assert_is_the_expected_frame(read_table(CSV_BYTES, filename="x.csv"))

    def test_the_csv_separator_is_configurable(self):
        comma = b"id,title\n1,Bruchrechnen\n"
        assert read_table(comma, fmt="csv", separator=",")["title"][0] == "Bruchrechnen"

    def test_the_csv_encoding_is_configurable(self):
        latin = "id;title\n1;Grün\n".encode("latin-1")
        assert read_table(latin, fmt="csv", encoding="latin-1")["title"][0] == "Grün"

    def test_an_empty_cell_is_an_empty_string_not_a_float(self):
        """The store promises all-strings; a NaN would break every operation
        that compares cells and would not survive the CSV round-trip."""
        df = read_table(b"a;b\n1;\n", fmt="csv")
        assert df["b"][0] == ""
        assert all(isinstance(v, str) for v in df.iloc[0])

    def test_the_literal_string_NA_stays_text(self):
        """A taxonid or keyword may legitimately be 'NA' -- pandas' default
        would silently turn it into a missing value."""
        assert read_table(b"a\nNA\n", fmt="csv")["a"][0] == "NA"

    def test_nested_json_arrives_flattened(self):
        raw = json.dumps([{"properties": {"cclom:title": ["Bruch"]}}]).encode("utf-8")
        assert read_table(raw, fmt="json")["properties.cclom:title"][0] == "Bruch"

    def test_records_with_different_keys_union_their_columns(self):
        """Two exports rarely carry identical fields; a missing one must become
        an empty cell rather than dropping the row or the column."""
        raw = b'{"a": "1"}\n{"b": "2"}\n'
        df = read_table(raw, fmt="jsonl")
        assert sorted(df.columns) == ["a", "b"]
        assert df.to_dict("records") == [{"a": "1", "b": ""}, {"a": "", "b": "2"}]

    def test_a_single_json_object_is_one_row(self):
        assert len(read_table(b'{"a": "1"}', fmt="json")) == 1

    def test_blank_lines_in_jsonl_are_skipped(self):
        assert len(read_table(b'{"a":"1"}\n\n{"a":"2"}\n', fmt="jsonl")) == 2

    def test_an_unreadable_payload_raises_a_client_safe_error(self):
        with pytest.raises(ValueError, match="not readable"):
            read_table(b"\x00\x01\x02", fmt="json")

    def test_an_empty_payload_raises_rather_than_returning_nothing(self):
        with pytest.raises(ValueError, match="no data rows"):
            read_table(b"", fmt="csv")

    def test_an_unknown_format_is_rejected_by_name(self):
        with pytest.raises(ValueError, match="parquet"):
            read_table(CSV_BYTES, fmt="parquet")


class TestWriteTable:
    @pytest.mark.parametrize("fmt", ["csv", "csv.gz", "json", "jsonl"])
    def test_the_round_trip_is_lossless(self, fmt):
        """Writing then reading must return the same table, or an export is a
        quiet data loss rather than a format conversion."""
        original = read_table(CSV_BYTES, fmt="csv")
        back = read_table(write_table(original, fmt=fmt), fmt=fmt.removesuffix(".gz") + (
            ".gz" if fmt.endswith(".gz") else ""))
        assert back.to_dict("records") == original.to_dict("records")
        assert list(back.columns) == list(original.columns)

    def test_the_csv_separator_is_configurable(self):
        out = write_table(read_table(CSV_BYTES, fmt="csv"), fmt="csv", separator=",")
        assert out.decode("utf-8").splitlines()[0] == "id,title,keywords"

    def test_gz_output_really_is_gzip(self):
        out = write_table(read_table(CSV_BYTES, fmt="csv"), fmt="csv.gz")
        assert out.startswith(b"\x1f\x8b")
        assert gzip.decompress(out).decode("utf-8").startswith("id;title;keywords")

    def test_a_cell_containing_the_separator_survives(self):
        """The keywords column holds commas; a comma-separated export must quote
        them or the round trip silently gains a column."""
        original = read_table(CSV_BYTES, fmt="csv")
        back = read_table(write_table(original, fmt="csv", separator=","),
                          fmt="csv", separator=",")
        assert back["keywords"][0] == "Mathematik,Bruch"

    def test_line_endings_are_unix(self):
        """The store is read back on Linux containers and Windows alike; pinning
        one ending keeps file hashes comparable across both."""
        out = write_table(read_table(CSV_BYTES, fmt="csv"), fmt="csv")
        assert b"\r\n" not in out

    def test_an_unsupported_write_format_is_rejected_by_name(self):
        with pytest.raises(ValueError, match="jsonl.gz"):
            write_table(read_table(CSV_BYTES, fmt="csv"), fmt="jsonl.gz")
