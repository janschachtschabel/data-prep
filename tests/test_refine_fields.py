"""How a text field's cell is read and written.

Keywords are the reason this exists — one cell, several values, comma-separated —
but the list-ness is a property of the FIELD, not a special case for keywords: a
semicolon-separated author list behaves identically. These tests pin the cases that
actually occur in exported metadata: blank cells, stray spaces, empty items, and a
single-valued field whose text happens to contain the separator.
"""

from __future__ import annotations

from app.refine.fields import TextField, is_gap, read_values, write_values


def test_a_single_valued_cell_is_one_value_even_with_a_comma_in_it():
    """A title reading "Algebra, Teil 1" is one title. Splitting it because a list
    field elsewhere uses commas would quietly cut every enumerating title in half."""
    title = TextField(column="title")

    assert read_values("Algebra, Teil 1", title) == ["Algebra, Teil 1"]
    assert read_values("  Algebra  ", title) == ["Algebra"]


def test_a_blank_cell_holds_no_values():
    """Blank, whitespace and a missing cell are the same thing to a reader: nothing
    is there. pandas hands back NaN for a missing cell, which is not a string."""
    title = TextField(column="title")
    keywords = TextField(column="keywords", separator=",")

    for empty in ("", "   ", None, float("nan")):
        assert read_values(empty, title) == [], f"{empty!r} should read as no values"
        assert read_values(empty, keywords) == [], f"{empty!r} should read as no values"


def test_a_list_cell_splits_and_drops_what_is_not_a_value():
    """Exports carry trailing separators and double commas; an empty string is not a
    keyword, and a keyword with spaces around it is the same keyword."""
    keywords = TextField(column="keywords", separator=",")

    assert read_values("Mathe, Algebra ,, Gleichung, ", keywords) == [
        "Mathe", "Algebra", "Gleichung"]


def test_writing_a_list_joins_it_the_way_the_field_separates_it():
    """Round trip: what write_values produces must read back as the same values, with
    the field's own separator rather than a hard-coded comma."""
    semicolons = TextField(column="authors", separator=";")

    cell = write_values(["Meier", "Schulz"], semicolons)
    assert cell == "Meier; Schulz"
    assert read_values(cell, semicolons) == ["Meier", "Schulz"]


def test_writing_a_single_valued_field_keeps_the_first_value_only():
    """A model asked for one description sometimes returns two. The field says one
    value, so one value is stored — silently keeping both would corrupt the column."""
    description = TextField(column="description")

    assert write_values(["erste", "zweite"], description) == "erste"
    assert write_values([], description) == ""


def test_a_gap_is_fewer_values_than_the_field_asks_for():
    """The reason min_values exists: three keywords is the threshold the shipped
    enrichment has always used, and two of them is still a gap."""
    keywords = TextField(column="keywords", separator=",", min_values=3)
    description = TextField(column="description")

    assert is_gap("Mathe, Algebra", keywords) is True
    assert is_gap("Mathe, Algebra, Gleichung", keywords) is False
    assert is_gap("", description) is True
    assert is_gap("ein Satz", description) is False
