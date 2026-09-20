"""The spreadsheet variant of a CSV: no cell of it runs as a formula.

Metadata harvested from the web can hold a cell like `=HYPERLINK("http://…")`,
which Excel and LibreOffice run when someone opens the file (OWASP CSV
injection). Only the downloads that ask for this variant get it -- the
apostrophe is part of the text for every program that reads the file back,
which is why the training CSV and the api_v3 push must not have it.
"""

from __future__ import annotations

import io

import pandas as pd
import pytest

from app.spreadsheet import FORMULA_TRIGGERS, defuse, spreadsheet_csv

HYPERLINK = '=HYPERLINK("http://evil.example","click")'


def _cells(text: str) -> pd.DataFrame:
    return pd.read_csv(io.StringIO(text), sep=";", dtype=str, keep_default_na=False)


def test_a_formula_cell_is_text_again():
    frame = pd.DataFrame({"title": [HYPERLINK, "Bruchrechnen"]})
    back = _cells(spreadsheet_csv(frame, sep=";"))
    assert list(back["title"]) == [f"'{HYPERLINK}", "Bruchrechnen"]


@pytest.mark.parametrize("trigger", FORMULA_TRIGGERS)
def test_every_trigger_a_spreadsheet_reads_as_a_formula_is_defused(trigger):
    """The OWASP list: `=` and `@` start a formula, `+`/`-` start one in Excel,
    and a leading tab or carriage return can move the rest into a cell of its own."""
    frame = pd.DataFrame({"c": [f"{trigger}1+1"]})
    assert defuse(f"{trigger}1+1") == f"'{trigger}1+1"
    assert list(_cells(spreadsheet_csv(frame, sep=";"))["c"]) == [f"'{trigger}1+1"]


def test_a_cell_that_is_no_formula_keeps_every_character():
    """Only the FIRST character decides: prefixing more than necessary would
    corrupt ordinary editorial text for the sake of nothing."""
    harmless = ["Bruchrechnen", "a=b", "", "Preis 5-10 Euro", "'schon Text"]
    frame = pd.DataFrame({"c": harmless})
    assert list(_cells(spreadsheet_csv(frame, sep=";"))["c"]) == harmless


def test_a_column_name_is_defused_like_a_cell():
    """Column names come from the uploaded data -- a JSON key or a CSV header
    can be a formula, and the header row is a row of cells like any other."""
    frame = pd.DataFrame({HYPERLINK: ["x"]})
    assert list(_cells(spreadsheet_csv(frame, sep=";")).columns) == [f"'{HYPERLINK}"]


def test_a_comma_inside_a_value_is_never_left_bare():
    """Excel in an English locale splits a semicolon CSV on COMMAS: an UNQUOTED
    `x,=1+1` opens as two cells there, the second one a formula. Quoting every
    field takes that simple form away (OWASP recommends both measures).

    What a comma-splitting parser makes of a quoted field it cannot delimit is
    its own affair -- the next test pins what the file holds, not what such a
    reader does with it."""
    text = spreadsheet_csv(pd.DataFrame({"c": ["x,=1+1"]}), sep=";")
    assert text.splitlines()[1] == '"x,=1+1"'
    assert text.splitlines()[0] == '"c"'


def test_every_field_of_a_row_is_quoted_and_every_cell_starts_defused():
    """The guarantee that holds for any reader: each cell this app writes is
    quoted, and the first character of each is defused. A `,=…` in the MIDDLE of
    a value is not a cell here, so nothing prefixes it -- only a reader that
    splits it out of its quotes could make one, which is why the apostrophe, not
    the quoting, is what this rests on."""
    text = spreadsheet_csv(pd.DataFrame({"a": ["x,=1+1"], "b": ["=SUM(1)"]}), sep=";")
    assert text.splitlines()[1] == '"x,=1+1";"\'=SUM(1)"'


def test_spaces_before_a_trigger_keep_the_cell_as_it_is():
    """A documented limit (README/CHANGELOG): ` =1+1` is text to Excel, and
    LibreOffice runs it only with "Trim spaces" AND "Evaluate formulas" on. Pinned
    so a change to the trigger check cannot move the limit unnoticed."""
    assert defuse(" =1+1") == " =1+1"


def test_the_apostrophe_is_part_of_the_text_when_a_program_reads_it_back():
    """Why this variant is asked for and never the default: api_v3 would train
    on the apostrophe (its clean_text keeps it), and so would a re-import here."""
    frame = pd.DataFrame({"title": [HYPERLINK]})
    assert _cells(spreadsheet_csv(frame, sep=";"))["title"][0].startswith("'=")


def test_the_separator_is_the_one_the_download_asked_for():
    text = spreadsheet_csv(pd.DataFrame({"a": ["1"], "b": ["2"]}), sep=",")
    assert text.splitlines()[0] == '"a","b"'


def test_the_caller_s_frame_is_left_alone():
    """The frame belongs to the caller (the refine store hands out the loaded
    dataset); defusing a copy keeps a download from changing what is stored."""
    frame = pd.DataFrame({HYPERLINK: [HYPERLINK]})
    spreadsheet_csv(frame, sep=";")
    assert list(frame.columns) == [HYPERLINK]
    assert frame.iloc[0, 0] == HYPERLINK
