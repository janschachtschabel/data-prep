"""Duplicates over chosen key columns — reporting them and removing them.

The existing dedupe filters key on the COMBINED TEXT, which answers "is this the
same material?". This answers a different question: "is this the same record?",
keyed on whatever identifies a row in the source system — a URL, an id, or a
pair of columns together.

The trap this module has to avoid is empty keys. A dataset where 50 rows have no
URL would see all 50 collapse into one if an empty key counted as a value. They
are therefore never duplicates of each other, and their number is reported.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.refine.duplicates import dedupe_keys, duplicate_report

DF = pd.DataFrame(
    {
        "url": ["a.de", "b.de", "a.de", "c.de", "a.de", "", ""],
        "titel": ["Erst", "Zwei", "Drei", "Vier", "Fuenf", "Sechs", "Sieben"],
        "quelle": ["x", "x", "y", "x", "x", "x", "x"],
    }
)


class TestReport:
    def test_it_counts_groups_and_rows(self):
        r = duplicate_report(DF, ["url"])
        assert r["duplicate_groups"] == 1        # only 'a.de' repeats
        assert r["duplicate_rows"] == 3          # the three a.de rows
        assert r["removable_rows"] == 2          # keeping one of them

    def test_it_reports_the_totals_it_was_given(self):
        r = duplicate_report(DF, ["url"])
        assert r["rows"] == 7
        assert r["keys"] == ["url"]

    def test_empty_keys_are_never_duplicates_of_each_other(self):
        """The trap: two rows without a URL are not the same record, and
        collapsing them would be silent data loss."""
        r = duplicate_report(DF, ["url"])
        assert r["empty_key_rows"] == 2
        assert r["duplicate_rows"] == 3  # the two blanks are not counted

    def test_several_keys_are_combined(self):
        """url+quelle: 'a.de' from source x twice, 'a.de' from y once."""
        r = duplicate_report(DF, ["url", "quelle"])
        assert r["duplicate_groups"] == 1
        assert r["duplicate_rows"] == 2

    def test_examples_name_the_offending_key_and_its_count(self):
        example = duplicate_report(DF, ["url"])["examples"][0]
        assert example["key"] == {"url": "a.de"}
        assert example["count"] == 3

    def test_the_number_of_examples_is_bounded(self):
        df = pd.DataFrame({"k": [str(i // 2) for i in range(40)]})
        assert len(duplicate_report(df, ["k"], examples=3)["examples"]) == 3

    def test_a_frame_without_duplicates_reports_zero(self):
        df = pd.DataFrame({"k": ["1", "2", "3"]})
        r = duplicate_report(df, ["k"])
        assert r["duplicate_groups"] == 0
        assert r["removable_rows"] == 0
        assert r["examples"] == []

    def test_an_unknown_key_column_names_what_is_available(self):
        with pytest.raises(ValueError, match="nope"):
            duplicate_report(DF, ["nope"])

    def test_no_key_is_refused(self):
        with pytest.raises(ValueError, match="at least one"):
            duplicate_report(DF, [])

    def test_it_is_json_safe(self):
        import json

        json.dumps(duplicate_report(DF, ["url", "quelle"]))

    def test_it_does_not_mutate_the_input(self):
        before = DF.copy()
        duplicate_report(DF, ["url"])
        pd.testing.assert_frame_equal(DF, before)


class TestDedupe:
    def test_it_keeps_the_first_of_each_group(self):
        new, _ = dedupe_keys(DF, {"keys": ["url"]}, {})
        assert list(new["titel"]) == ["Erst", "Zwei", "Vier", "Sechs", "Sieben"]

    def test_rows_without_a_key_all_survive(self):
        new, _ = dedupe_keys(DF, {"keys": ["url"]}, {})
        assert list(new["titel"]).count("Sechs") == 1
        assert "Sieben" in list(new["titel"])

    def test_keep_last_is_available(self):
        """Sources are often ordered oldest-first; the newest record wins."""
        new, _ = dedupe_keys(DF, {"keys": ["url"], "keep": "last"}, {})
        assert "Fuenf" in list(new["titel"])
        assert "Erst" not in list(new["titel"])

    def test_the_stats_shape_matches_the_other_operations(self):
        _, stats = dedupe_keys(DF, {"keys": ["url"]}, {})
        assert stats["filter"] == "dedupe_keys"
        assert stats["before"] == 7
        assert stats["after"] == 5
        assert stats["removed"] == 2
        assert stats["changed"] == 0

    def test_removed_examples_show_what_went(self):
        _, stats = dedupe_keys(DF, {"keys": ["url"]}, {})
        assert stats["examples"]
        assert any("a.de" in e["removed"] for e in stats["examples"])

    def test_an_unknown_keep_mode_is_refused(self):
        with pytest.raises(ValueError, match="first.*last|last.*first"):
            dedupe_keys(DF, {"keys": ["url"], "keep": "beste"}, {})

    def test_an_unknown_key_column_is_refused(self):
        with pytest.raises(ValueError, match="nope"):
            dedupe_keys(DF, {"keys": ["nope"]}, {})

    def test_no_key_is_refused(self):
        with pytest.raises(ValueError, match="at least one"):
            dedupe_keys(DF, {"keys": []}, {})

    def test_it_does_not_mutate_the_input(self):
        before = DF.copy()
        dedupe_keys(DF, {"keys": ["url"]}, {})
        pd.testing.assert_frame_equal(DF, before)

    def test_the_index_is_reset_so_later_steps_line_up(self):
        new, _ = dedupe_keys(DF, {"keys": ["url"]}, {})
        assert list(new.index) == list(range(len(new)))


class TestIncompleteCompositeKeys:
    """A composite key with one part missing is INCOMPLETE, not a value.

    Two rows that merely share a source and both lack a URL are not the same
    record. Getting this wrong loses rows silently, which is why it is pinned
    separately from the plain empty-key case.
    """

    DF = pd.DataFrame({
        "url": ["a.de", "a.de", "", ""],
        "quelle": ["x", "x", "x", "x"],
        "titel": ["Erst", "Zwei", "Drei", "Vier"],
    })

    def test_rows_with_a_missing_key_part_do_not_collide(self):
        r = duplicate_report(self.DF, ["url", "quelle"])
        assert r["duplicate_groups"] == 1     # only (a.de, x)
        assert r["duplicate_rows"] == 2
        assert r["empty_key_rows"] == 2       # the two without a URL

    def test_dedupe_keeps_every_row_with_an_incomplete_key(self):
        new, stats = dedupe_keys(self.DF, {"keys": ["url", "quelle"]}, {})
        assert list(new["titel"]) == ["Erst", "Drei", "Vier"]
        assert stats["removed"] == 1
