"""The CSV variant meant for people: one that no spreadsheet runs as a formula.

A cell beginning with ``=``, ``+``, ``-``, ``@``, a tab or a carriage return is
a formula to Excel and LibreOffice, not text (OWASP "CSV injection"). Metadata
harvested from the web reaches this app as data -- a title like
``=HYPERLINK("http://…","click")`` puts one click between a reader and an
attacker's page.

The defusing apostrophe is part of the TEXT for every program that reads the
file back: api_v3 trains on it (its ``clean_text`` keeps it) and so does an
import here. That is why this is a variant a download asks for, never what the
training CSV or the api_v3 push contain -- those stay byte-identical.
"""

from __future__ import annotations

import csv

import pandas as pd

# OWASP's list of characters a spreadsheet reads as the start of a formula.
FORMULA_TRIGGERS = ("=", "+", "-", "@", "\t", "\r")


def defuse(value: str) -> str:
    """``value`` as a spreadsheet shows it: text, not a formula.

    Only the first character decides. Prefixing anything else would corrupt
    ordinary editorial text without protecting anyone.
    """
    return f"'{value}" if value.startswith(FORMULA_TRIGGERS) else value


def spreadsheet_csv(frame: pd.DataFrame, *, sep: str) -> str:
    """``frame`` as CSV text safe to open in Excel or LibreOffice.

    Header cells are defused like any other: column names come from the
    uploaded data, and the header is a row of cells too.

    Every field is quoted because a spreadsheet may split on another separator
    than the one written here -- Excel in an English locale splits a semicolon
    CSV on commas, which would make a second cell out of ``x,=1+1`` and run it.
    """
    # A copy: the frame belongs to the caller (the refine store hands out the
    # loaded dataset), and a download must not change what is stored.
    safe = frame.map(lambda cell: defuse(cell) if isinstance(cell, str) else cell)
    safe.columns = [defuse(str(name)) for name in safe.columns]
    return safe.to_csv(sep=sep, index=False, lineterminator="\n", quoting=csv.QUOTE_ALL)
