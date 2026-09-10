"""Keeping, dropping and renaming columns.

Small operations, but they are what makes a flattened export usable: a WLO
record expands to well over a hundred dot-path columns and only a handful are
training material. Every rejection here has to be a 400-shaped ValueError, not
a KeyError surfacing as a 500.
"""

from __future__ import annotations

import pandas as pd
import pytest

from app.refine.columns import drop_columns, rename_columns, select_columns

DF = pd.DataFrame(
    {
        "id": ["1", "2"],
        "properties.cclom:title": ["Bruchrechnen", "Photosynthese"],
        "properties.ccm:taxonid": ["disc/380", "disc/080"],
        "internal.debug": ["x", "y"],
    }
)


class TestSelect:
    def test_it_keeps_only_the_named_columns(self):
        new, _ = select_columns(DF, {"keep": ["id", "properties.cclom:title"]}, {})
        assert list(new.columns) == ["id", "properties.cclom:title"]

    def test_the_given_order_is_the_new_order(self):
        """Column order is what the exported CSV looks like, so it is a choice
        the operator makes rather than an accident of the source file."""
        new, _ = select_columns(DF, {"keep": ["properties.cclom:title", "id"]}, {})
        assert list(new.columns) == ["properties.cclom:title", "id"]

    def test_rows_are_untouched(self):
        new, stats = select_columns(DF, {"keep": ["id"]}, {})
        assert len(new) == 2
        assert stats["removed"] == 0

    def test_stats_name_the_columns_on_both_sides(self):
        _, stats = select_columns(DF, {"keep": ["id"]}, {})
        assert stats["filter"] == "select_columns"
        assert stats["columns_before"] == list(DF.columns)
        assert stats["columns_after"] == ["id"]
        assert stats["changed"] == 3  # three dropped

    def test_an_unknown_column_names_what_is_available(self):
        with pytest.raises(ValueError, match="titel"):
            select_columns(DF, {"keep": ["titel"]}, {})

    def test_keeping_nothing_is_refused(self):
        with pytest.raises(ValueError, match="at least one"):
            select_columns(DF, {"keep": []}, {})

    def test_a_repeated_column_is_refused(self):
        """pandas would happily produce two columns with one name, and every
        later lookup would then be ambiguous."""
        with pytest.raises(ValueError, match="twice|duplicate"):
            select_columns(DF, {"keep": ["id", "id"]}, {})

    def test_it_does_not_mutate_the_input(self):
        before = DF.copy()
        select_columns(DF, {"keep": ["id"]}, {})
        pd.testing.assert_frame_equal(DF, before)


class TestDrop:
    def test_it_removes_the_named_columns(self):
        new, _ = drop_columns(DF, {"drop": ["internal.debug"]}, {})
        assert "internal.debug" not in new.columns
        assert len(new.columns) == 3

    def test_the_surviving_order_is_preserved(self):
        new, _ = drop_columns(DF, {"drop": ["properties.ccm:taxonid"]}, {})
        assert list(new.columns) == ["id", "properties.cclom:title", "internal.debug"]

    def test_an_unknown_column_is_refused(self):
        with pytest.raises(ValueError, match="nope"):
            drop_columns(DF, {"drop": ["nope"]}, {})

    def test_dropping_everything_is_refused(self):
        """An empty frame is not a dataset; the store cannot even list it."""
        with pytest.raises(ValueError, match="every column|at least one"):
            drop_columns(DF, {"drop": list(DF.columns)}, {})

    def test_dropping_nothing_is_refused(self):
        with pytest.raises(ValueError, match="at least one"):
            drop_columns(DF, {"drop": []}, {})

    def test_it_does_not_mutate_the_input(self):
        before = DF.copy()
        drop_columns(DF, {"drop": ["id"]}, {})
        pd.testing.assert_frame_equal(DF, before)


class TestRename:
    def test_it_renames_and_keeps_position(self):
        new, _ = rename_columns(DF, {"mapping": {"properties.cclom:title": "title"}}, {})
        assert list(new.columns) == ["id", "title", "properties.ccm:taxonid", "internal.debug"]

    def test_values_travel_with_the_name(self):
        new, _ = rename_columns(DF, {"mapping": {"properties.cclom:title": "title"}}, {})
        assert list(new["title"]) == ["Bruchrechnen", "Photosynthese"]

    def test_stats_report_how_many_moved(self):
        _, stats = rename_columns(DF, {"mapping": {"id": "identifier"}}, {})
        assert stats["filter"] == "rename_columns"
        assert stats["changed"] == 1
        assert stats["removed"] == 0

    def test_an_unknown_source_column_is_refused(self):
        with pytest.raises(ValueError, match="nope"):
            rename_columns(DF, {"mapping": {"nope": "x"}}, {})

    def test_renaming_onto_an_existing_column_is_refused(self):
        """Two columns of one name make every later lookup ambiguous."""
        with pytest.raises(ValueError, match="already"):
            rename_columns(DF, {"mapping": {"id": "internal.debug"}}, {})

    def test_two_columns_renamed_to_the_same_name_is_refused(self):
        with pytest.raises(ValueError, match="twice|duplicate|already"):
            rename_columns(DF, {"mapping": {"id": "x", "internal.debug": "x"}}, {})

    def test_an_empty_new_name_is_refused(self):
        with pytest.raises(ValueError, match="empty"):
            rename_columns(DF, {"mapping": {"id": "  "}}, {})

    def test_an_empty_mapping_is_refused(self):
        with pytest.raises(ValueError, match="at least one"):
            rename_columns(DF, {"mapping": {}}, {})

    def test_a_no_op_rename_is_allowed(self):
        """Renaming a column to its own name changes nothing and is harmless;
        refusing it would only annoy someone editing a mapping in the UI."""
        new, stats = rename_columns(DF, {"mapping": {"id": "id"}}, {})
        assert list(new.columns) == list(DF.columns)
        assert stats["changed"] == 0

    def test_it_does_not_mutate_the_input(self):
        before = DF.copy()
        rename_columns(DF, {"mapping": {"id": "identifier"}}, {})
        pd.testing.assert_frame_equal(DF, before)
