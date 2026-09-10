"""Per-column statistics for an arbitrary table.

`analyze` already reports label support and text lengths, but only once someone
has said which column is the label. This profile answers the question that
comes first: what IS in this file? Fill rate, cardinality, the common values,
and real numbers where a column holds numbers.
"""

from __future__ import annotations

import pandas as pd

from app.refine.profile import profile_columns

DF = pd.DataFrame(
    {
        "typ": ["video", "video", "quiz", "video", ""],
        "jahr": ["2015", "2009", "2020", "2020", "2020"],
        "notiz": ["", "", "", "", ""],
        "gemischt": ["1", "2", "drei", "4", "5"],
    }
)


def _column(profile: dict, name: str) -> dict:
    return next(c for c in profile["columns"] if c["name"] == name)


class TestShape:
    def test_it_reports_the_row_count_once(self):
        assert profile_columns(DF)["rows"] == 5

    def test_every_column_appears_in_source_order(self):
        names = [c["name"] for c in profile_columns(DF)["columns"]]
        assert names == ["typ", "jahr", "notiz", "gemischt"]

    def test_an_empty_frame_profiles_to_nothing_without_crashing(self):
        profile = profile_columns(pd.DataFrame({"a": []}))
        assert profile["rows"] == 0
        assert _column(profile, "a")["filled"] == 0


class TestFillAndCardinality:
    def test_an_empty_cell_counts_as_empty_not_as_a_value(self):
        typ = _column(profile_columns(DF), "typ")
        assert typ["filled"] == 4
        assert typ["empty"] == 1
        assert typ["fill_rate"] == 0.8

    def test_distinct_ignores_empty_cells(self):
        """Otherwise 'how many kinds of thing are in here' is always off by one
        for any column with a gap."""
        assert _column(profile_columns(DF), "typ")["distinct"] == 2

    def test_a_wholly_empty_column_is_marked_as_such(self):
        notiz = _column(profile_columns(DF), "notiz")
        assert notiz["kind"] == "empty"
        assert notiz["fill_rate"] == 0.0
        assert notiz["distinct"] == 0


class TestTopValues:
    def test_the_most_common_values_come_first_with_counts(self):
        top = _column(profile_columns(DF), "typ")["top_values"]
        assert top[0] == {"value": "video", "count": 3}
        assert top[1] == {"value": "quiz", "count": 1}

    def test_top_n_is_honoured(self):
        top = _column(profile_columns(DF, top_n=1), "typ")["top_values"]
        assert len(top) == 1

    def test_empty_cells_are_not_a_top_value(self):
        values = [t["value"] for t in _column(profile_columns(DF), "typ")["top_values"]]
        assert "" not in values


class TestNumericSummary:
    def test_a_numeric_column_gets_real_numbers(self):
        jahr = _column(profile_columns(DF), "jahr")
        assert jahr["kind"] == "numeric"
        assert jahr["numeric"]["min"] == 2009
        assert jahr["numeric"]["max"] == 2020
        assert jahr["numeric"]["median"] == 2020

    def test_a_text_column_has_no_numeric_block(self):
        assert _column(profile_columns(DF), "typ")["numeric"] is None

    def test_a_mostly_numeric_column_is_still_summarised(self):
        """One stray word should not hide the range of a year column; the
        summary says how many cells it could actually read."""
        gemischt = _column(profile_columns(DF), "gemischt")
        assert gemischt["kind"] == "numeric"
        assert gemischt["numeric"]["parsed"] == 4
        assert gemischt["numeric"]["max"] == 5

    def test_a_mostly_textual_column_is_not_called_numeric(self):
        df = pd.DataFrame({"x": ["1", "zwei", "drei", "vier", "fünf"]})
        assert _column(profile_columns(df), "x")["kind"] == "text"

    def test_percentiles_are_reported(self):
        df = pd.DataFrame({"n": [str(i) for i in range(1, 101)]})
        numeric = _column(profile_columns(df), "n")["numeric"]
        assert numeric["p05"] < numeric["median"] < numeric["p95"]


class TestPurity:
    def test_it_does_not_mutate_the_input(self):
        before = DF.copy()
        profile_columns(DF)
        pd.testing.assert_frame_equal(DF, before)

    def test_every_reported_number_is_json_safe(self):
        """The profile is serialised straight to the UI; a numpy scalar or a
        NaN would fail json.dumps or render as a JavaScript null."""
        import json

        json.dumps(profile_columns(DF))
