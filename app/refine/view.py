"""One page of rows, with an optional search.

Separate from ``profile.py``: that describes COLUMNS, this shows ROWS. Both are
read-only, and neither belongs in a route -- the constitution keeps routes to
auth, validation and HTTP mapping.

Only the requested page is materialised. A 400k-row table serialised whole would
be a multi-hundred-megabyte response, and the viewer exists precisely so nobody
has to load a dataset to look at it.
"""

from __future__ import annotations

import pandas as pd

from .keys import require_keys


def page_rows(
    df: pd.DataFrame,
    *,
    offset: int = 0,
    limit: int = 50,
    query: str = "",
    columns: list[str] | None = None,
) -> dict:
    """Rows ``offset``..``offset+limit`` of ``df``, optionally filtered by a
    case-insensitive substring.

    ``total`` and ``matched`` are both returned: "30 of 60" is how an operator
    sees that a search did what they meant. An offset past the end yields an
    empty page rather than an error -- paging off the end is normal.

    Raises ``ValueError`` (client-safe) for an unknown or repeated column.
    """
    if columns:
        require_keys(df, columns, what="column")
        repeated = sorted({c for c in columns if columns.count(c) > 1})
        if repeated:
            # pandas would return two columns of one name and to_dict would
            # silently keep the last.
            raise ValueError(f"Column named twice: {', '.join(map(repr, repeated))}.")
    view = df[columns] if columns else df
    total = int(len(df))

    if query:
        needle = query.lower()
        # Search only the VISIBLE columns: matching on a hidden one would show
        # rows with no apparent reason to be there.
        hit = view.apply(
            lambda column: column.fillna("").astype(str).str.lower()
            .str.contains(needle, regex=False)
        ).any(axis=1)
        view = view[hit]

    matched = int(len(view))
    page = view.iloc[offset : offset + limit]
    return {
        "total": total,
        "matched": matched,
        "offset": offset,
        "limit": limit,
        "columns": list(view.columns),
        "rows": page.fillna("").astype(str).to_dict("records"),
    }
