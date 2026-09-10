"""Joining two datasets on one or more keys.

`combine_datasets` already exists and does something different: it maps columns
onto a target schema and CONCATENATES, resolving text duplicates. This matches
rows to rows — the "reconcile two exports on an id" case.

The failure mode a join has and a concatenation does not is the row explosion.
A key that repeats three times on the left and four on the right produces twelve
rows for that key alone, and on a dirty key a join can exhaust memory. So the
cardinality is reported BEFORE the join runs, and the join refuses to exceed a
ceiling rather than trying and dying.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.refine.join import join_datasets, key_cardinality

LEFT = pd.DataFrame({
    "id": ["1", "2", "3", ""],
    "titel": ["Bruchrechnen", "Photosynthese", "Wiener Kongress", "Ohne Id"],
})
RIGHT = pd.DataFrame({
    "uid": ["1", "2", "4", ""],
    "fach": ["Mathematik", "Biologie", "Chemie", "Ohne Id"],
})
KEYS = [{"left": "id", "right": "uid"}]


class TestCardinality:
    def test_it_counts_both_sides_and_the_overlap(self):
        r = key_cardinality(LEFT, RIGHT, KEYS)
        assert r["left_rows"] == 4
        assert r["right_rows"] == 4
        assert r["matching_keys"] == 2       # '1' and '2'
        assert r["left_only_keys"] == 1      # '3'
        assert r["right_only_keys"] == 1     # '4'

    def test_it_names_the_relation(self):
        assert key_cardinality(LEFT, RIGHT, KEYS)["relation"] == "one-to-one"

    def test_it_detects_a_many_to_many_relation(self):
        left = pd.DataFrame({"k": ["a", "a", "a"], "v": ["1", "2", "3"]})
        right = pd.DataFrame({"k": ["a", "a"], "w": ["x", "y"]})
        r = key_cardinality(left, right, [{"left": "k", "right": "k"}])
        assert r["relation"] == "many-to-many"
        assert r["left_max_repeat"] == 3
        assert r["right_max_repeat"] == 2

    def test_it_predicts_the_row_count_before_the_join_runs(self):
        """3 x 2 for the one shared key — the number that makes a join dangerous."""
        left = pd.DataFrame({"k": ["a", "a", "a"], "v": ["1", "2", "3"]})
        right = pd.DataFrame({"k": ["a", "a"], "w": ["x", "y"]})
        r = key_cardinality(left, right, [{"left": "k", "right": "k"}])
        assert r["estimated_rows"] == 6
        assert r["explodes"] is True

    def test_a_clean_join_does_not_count_as_exploding(self):
        assert key_cardinality(LEFT, RIGHT, KEYS)["explodes"] is False

    def test_rows_with_an_incomplete_key_are_excluded_from_the_counts(self):
        """The empty-id row on each side must not look like a match."""
        r = key_cardinality(LEFT, RIGHT, KEYS)
        assert r["left_unusable_key_rows"] == 1
        assert r["right_unusable_key_rows"] == 1
        assert r["matching_keys"] == 2

    def test_an_unknown_column_is_refused_naming_the_side(self):
        with pytest.raises(ValueError, match="nope"):
            key_cardinality(LEFT, RIGHT, [{"left": "nope", "right": "uid"}])


class TestJoin:
    def test_left_join_keeps_every_left_row(self):
        new, stats = join_datasets(LEFT, RIGHT, keys=KEYS, how="left")
        assert len(new) == 4
        assert list(new["titel"]) == ["Bruchrechnen", "Photosynthese",
                                      "Wiener Kongress", "Ohne Id"]

    def test_an_unmatched_left_row_gets_empty_cells_not_nan(self):
        """A NaN would break the all-strings promise every operation relies on."""
        new, _ = join_datasets(LEFT, RIGHT, keys=KEYS, how="left")
        assert new.loc[new["titel"] == "Wiener Kongress", "fach"].iloc[0] == ""
        assert all(isinstance(v, str) for v in new["fach"])

    def test_inner_join_keeps_only_matches(self):
        new, _ = join_datasets(LEFT, RIGHT, keys=KEYS, how="inner")
        assert list(new["titel"]) == ["Bruchrechnen", "Photosynthese"]

    def test_right_join_keeps_every_right_row(self):
        new, _ = join_datasets(LEFT, RIGHT, keys=KEYS, how="right")
        assert len(new) == 4
        assert "Chemie" in list(new["fach"])

    def test_outer_join_keeps_everything(self):
        new, _ = join_datasets(LEFT, RIGHT, keys=KEYS, how="outer")
        assert len(new) == 6  # 2 matched + 1 left-only + 1 right-only + 2 unusable

    def test_a_row_with_an_incomplete_key_never_matches(self):
        """Both sides have an empty id; joining them would be a false match."""
        new, _ = join_datasets(LEFT, RIGHT, keys=KEYS, how="left")
        assert new.loc[new["titel"] == "Ohne Id", "fach"].iloc[0] == ""

    def test_the_right_key_column_is_not_duplicated(self):
        new, _ = join_datasets(LEFT, RIGHT, keys=KEYS, how="left")
        assert "uid" not in new.columns
        assert "id" in new.columns

    def test_stats_report_both_sides_and_what_matched(self):
        _, stats = join_datasets(LEFT, RIGHT, keys=KEYS, how="left")
        assert stats["before"] == 4
        assert stats["right_rows"] == 4
        assert stats["matched_rows"] == 2
        assert stats["unmatched_left"] == 2   # '3' and the empty id
        assert stats["unmatched_right"] == 2
        assert stats["filter"] == "join"


class TestColumnCollisions:
    LEFT2 = pd.DataFrame({"id": ["1", "2"], "fach": ["Mathe", ""]})
    RIGHT2 = pd.DataFrame({"id": ["1", "2"], "fach": ["Mathematik", "Biologie"]})
    SAME = [{"left": "id", "right": "id"}]

    def test_a_colliding_column_is_suffixed(self):
        new, stats = join_datasets(self.LEFT2, self.RIGHT2, keys=self.SAME, how="left")
        assert list(new.columns) == ["id", "fach", "fach_right"]
        assert stats["collided_columns"] == ["fach"]

    def test_the_suffix_is_configurable(self):
        new, _ = join_datasets(self.LEFT2, self.RIGHT2, keys=self.SAME,
                               how="left", suffix="_neu")
        assert "fach_neu" in new.columns

    def test_coalesce_fills_the_gaps_instead_of_adding_a_column(self):
        """The common intent when enriching: keep what is there, fill what is not."""
        new, _ = join_datasets(self.LEFT2, self.RIGHT2, keys=self.SAME,
                               how="left", coalesce=True)
        assert list(new.columns) == ["id", "fach"]
        assert list(new["fach"]) == ["Mathe", "Biologie"]


class TestRefusals:
    def test_it_refuses_to_exceed_the_row_ceiling(self):
        """Refusing beats trying and exhausting memory; the message says how big
        it would have been."""
        left = pd.DataFrame({"k": ["a"] * 50, "v": ["1"] * 50})
        right = pd.DataFrame({"k": ["a"] * 50, "w": ["2"] * 50})
        with pytest.raises(ValueError, match="2500"):
            join_datasets(left, right, keys=[{"left": "k", "right": "k"}],
                          how="inner", max_rows=1000)

    def test_an_unknown_join_type_is_refused(self):
        with pytest.raises(ValueError, match="inner"):
            join_datasets(LEFT, RIGHT, keys=KEYS, how="diagonal")

    def test_no_key_is_refused(self):
        with pytest.raises(ValueError, match="at least one"):
            join_datasets(LEFT, RIGHT, keys=[], how="left")

    def test_a_malformed_key_pair_is_refused(self):
        with pytest.raises(ValueError, match="left.*right|right.*left"):
            join_datasets(LEFT, RIGHT, keys=[{"left": "id"}], how="left")

    def test_it_does_not_mutate_either_input(self):
        before_left, before_right = LEFT.copy(), RIGHT.copy()
        join_datasets(LEFT, RIGHT, keys=KEYS, how="outer")
        pd.testing.assert_frame_equal(LEFT, before_left)
        pd.testing.assert_frame_equal(RIGHT, before_right)


class TestRightOnlyRowsKeepTheirKey:
    """A right-only row must still carry the key it was matched on.

    Dropping the right side's key column before the merge leaves those rows with
    an empty id — the result then contains rows nothing can identify, and a
    second join on the same key silently loses them.
    """

    def test_a_right_join_carries_the_key_across(self):
        new, _ = join_datasets(LEFT, RIGHT, keys=KEYS, how="right")
        chemie = new.loc[new["fach"] == "Chemie"]
        assert chemie["id"].iloc[0] == "4"

    def test_an_outer_join_carries_the_key_across(self):
        new, _ = join_datasets(LEFT, RIGHT, keys=KEYS, how="outer")
        chemie = new.loc[new["fach"] == "Chemie"]
        assert chemie["id"].iloc[0] == "4"

    def test_matched_rows_keep_the_left_key(self):
        new, _ = join_datasets(LEFT, RIGHT, keys=KEYS, how="outer")
        assert new.loc[new["titel"] == "Bruchrechnen", "id"].iloc[0] == "1"
