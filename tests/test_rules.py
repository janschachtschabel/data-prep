"""Filtering rows by what is IN a column, on any column.

The existing filters all read the combined text or the label column. These
rules read an arbitrary cell, which raises a question the label filters never
had to answer: the store is all-strings, so is "9" greater than "10"?

The contract pinned here: a rule comparing against a JSON NUMBER compares
numerically, a rule comparing against a STRING compares as text. That makes
`jahr >= 2015` do arithmetic and `datum >= "2026-01-01"` do the lexicographic
comparison an ISO date wants, without anyone declaring column types.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.refine.rules import OPERATORS, evaluate, filter_rows

DISCIPLINE = "http://w3id.org/openeduhub/vocabs/discipline/"
HOCHSCHUL = "http://w3id.org/openeduhub/vocabs/hochschulfaechersystematik/"

DF = pd.DataFrame(
    {
        "title": ["Bruchrechnen", "Photosynthese", "Der Wiener Kongress", ""],
        "jahr": ["2015", "2009", "2020", ""],
        "taxonid": [f"{DISCIPLINE}380", f"{DISCIPLINE}080", f"{HOCHSCHUL}1", "n/a"],
        "typ": ["video", "arbeitsblatt", "video", "quiz"],
    }
)


def _kept(rules, combine="and", df=DF):
    return list(df[evaluate(df, rules, combine=combine)]["title"])


class TestComparison:
    def test_eq_on_text(self):
        assert _kept([{"column": "typ", "op": "eq", "value": "video"}]) == [
            "Bruchrechnen", "Der Wiener Kongress"]

    def test_ne_on_text(self):
        assert "Bruchrechnen" not in _kept([{"column": "typ", "op": "ne", "value": "video"}])

    def test_a_numeric_value_compares_numerically(self):
        """The reason this module exists: as text, "9" sorts after "10"."""
        df = pd.DataFrame({"title": ["neun", "zehn"], "n": ["9", "10"]})
        assert _kept([{"column": "n", "op": "gt", "value": 9}], df=df) == ["zehn"]

    def test_a_string_value_compares_as_text(self):
        """ISO dates want exactly this, and they have no numeric reading."""
        df = pd.DataFrame({"title": ["alt", "neu"], "d": ["2025-12-31", "2026-02-01"]})
        assert _kept([{"column": "d", "op": "gte", "value": "2026-01-01"}], df=df) == ["neu"]

    @pytest.mark.parametrize(
        ("op", "value", "expected"),
        [
            ("lt", 2015, ["Photosynthese"]),
            ("lte", 2015, ["Bruchrechnen", "Photosynthese"]),
            ("gt", 2015, ["Der Wiener Kongress"]),
            ("gte", 2015, ["Bruchrechnen", "Der Wiener Kongress"]),
        ],
    )
    def test_the_four_orderings(self, op, value, expected):
        assert _kept([{"column": "jahr", "op": op, "value": value}]) == expected

    def test_between_is_inclusive_on_both_ends(self):
        rule = {"column": "jahr", "op": "between", "value": 2015, "value2": 2020}
        assert _kept([rule]) == ["Bruchrechnen", "Der Wiener Kongress"]

    def test_a_cell_that_is_not_a_number_never_matches_a_numeric_rule(self):
        """The empty 'jahr' cell must not silently count as zero."""
        assert "" not in _kept([{"column": "jahr", "op": "lt", "value": 9999}])


class TestMembershipAndText:
    def test_in_takes_a_list(self):
        rule = {"column": "typ", "op": "in", "value": ["quiz", "arbeitsblatt"]}
        assert _kept([rule]) == ["Photosynthese", ""]

    def test_not_in_is_the_complement(self):
        rule = {"column": "typ", "op": "not_in", "value": ["quiz", "arbeitsblatt"]}
        assert _kept([rule]) == ["Bruchrechnen", "Der Wiener Kongress"]

    def test_contains_is_case_insensitive_by_default(self):
        assert _kept([{"column": "title", "op": "contains", "value": "wiener"}]) == [
            "Der Wiener Kongress"]

    def test_contains_can_be_made_case_sensitive(self):
        rule = {"column": "title", "op": "contains", "value": "wiener", "case_sensitive": True}
        assert _kept([rule]) == []

    def test_starts_with_selects_a_uri_stem(self):
        """The URI-stem case: one taxonid field carries two vocabularies, and
        only a prefix separates school subjects from higher-education ones."""
        rule = {"column": "taxonid", "op": "starts_with", "value": DISCIPLINE}
        assert _kept([rule]) == ["Bruchrechnen", "Photosynthese"]

    def test_ends_with(self):
        assert _kept([{"column": "taxonid", "op": "ends_with", "value": "/380"}]) == [
            "Bruchrechnen"]

    def test_regex(self):
        rule = {"column": "title", "op": "regex", "value": r"^Der\s+\w+"}
        assert _kept([rule]) == ["Der Wiener Kongress"]

    def test_an_overlong_regex_is_refused(self):
        """A pattern is attacker-controlled input; an unbounded one can
        backtrack catastrophically and burn a core."""
        with pytest.raises(ValueError, match="200"):
            evaluate(DF, [{"column": "title", "op": "regex", "value": "a" * 201}])

    def test_an_invalid_regex_is_a_value_error_not_a_crash(self):
        with pytest.raises(ValueError, match="regular expression"):
            evaluate(DF, [{"column": "title", "op": "regex", "value": "("}])


class TestEmptiness:
    def test_is_empty(self):
        assert _kept([{"column": "title", "op": "is_empty"}]) == [""]

    def test_not_empty(self):
        assert len(_kept([{"column": "title", "op": "not_empty"}])) == 3

    def test_whitespace_counts_as_empty(self):
        """A cell of spaces is empty to a human, and imports produce them."""
        df = pd.DataFrame({"title": ["a"], "x": ["   "]})
        assert _kept([{"column": "x", "op": "is_empty"}], df=df) == ["a"]


class TestCombining:
    def test_and_requires_every_rule(self):
        rules = [
            {"column": "typ", "op": "eq", "value": "video"},
            {"column": "jahr", "op": "gte", "value": 2020},
        ]
        assert _kept(rules) == ["Der Wiener Kongress"]

    def test_or_requires_any_rule(self):
        rules = [
            {"column": "typ", "op": "eq", "value": "quiz"},
            {"column": "jahr", "op": "lt", "value": 2010},
        ]
        assert _kept(rules, combine="or") == ["Photosynthese", ""]

    def test_a_single_rule_behaves_the_same_either_way(self):
        rule = [{"column": "typ", "op": "eq", "value": "video"}]
        assert _kept(rule, combine="and") == _kept(rule, combine="or")


class TestRejections:
    def test_an_unknown_column_names_what_is_available(self):
        with pytest.raises(ValueError, match="jahrgang"):
            evaluate(DF, [{"column": "jahrgang", "op": "eq", "value": "x"}])

    def test_an_unknown_operator_lists_the_known_ones(self):
        with pytest.raises(ValueError, match="starts_with"):
            evaluate(DF, [{"column": "typ", "op": "beginnt_mit", "value": "v"}])

    def test_no_rules_is_refused_rather_than_matching_everything(self):
        """Silently keeping every row would look like a working filter."""
        with pytest.raises(ValueError, match="at least one rule"):
            evaluate(DF, [])

    def test_an_unknown_combine_mode_is_refused(self):
        with pytest.raises(ValueError, match="and.*or|or.*and"):
            evaluate(DF, [{"column": "typ", "op": "eq", "value": "video"}], combine="xor")

    def test_between_without_a_second_value_is_refused(self):
        with pytest.raises(ValueError, match="value2"):
            evaluate(DF, [{"column": "jahr", "op": "between", "value": 2015}])

    def test_in_without_a_list_is_refused(self):
        with pytest.raises(ValueError, match="list"):
            evaluate(DF, [{"column": "typ", "op": "in", "value": "video"}])

    def test_every_declared_operator_is_actually_dispatchable(self):
        """OPERATORS is published to the UI; an entry with no implementation
        would offer the user a rule that always errors."""
        for op in OPERATORS:
            rule = {"column": "typ", "op": op, "value": "video", "value2": "z"}
            if op in ("in", "not_in"):
                rule["value"] = ["video"]
            evaluate(DF, [rule])


class TestFilterRows:
    def test_it_returns_the_stats_shape_the_ops_history_stores(self):
        params = {"rules": [{"column": "typ", "op": "eq", "value": "video"}], "combine": "and"}
        new_df, stats = filter_rows(DF, params, {})
        assert len(new_df) == 2
        assert stats["before"] == 4
        assert stats["after"] == 2
        assert stats["removed"] == 2
        assert stats["changed"] == 0
        assert stats["filter"] == "rules"

    def test_it_reports_how_many_cells_were_not_comparable(self):
        """A numeric rule against a column of mixed text silently drops rows;
        the count is how an operator notices a wrong column."""
        params = {"rules": [{"column": "jahr", "op": "gt", "value": 0}]}
        _, stats = filter_rows(DF, params, {})
        assert stats["not_comparable"] == 1  # the empty 'jahr' cell

    def test_it_does_not_mutate_the_input(self):
        before = DF.copy()
        filter_rows(DF, {"rules": [{"column": "typ", "op": "eq", "value": "video"}]}, {})
        pd.testing.assert_frame_equal(DF, before)
