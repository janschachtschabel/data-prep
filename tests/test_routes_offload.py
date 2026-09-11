"""The heavy request bodies run off the event loop (audit 2026-09-11, P1).

One worker, one loop: while a 20 MB gzip import inflates and parses inline,
/health and run polling wait, and the container health check (5 s timeout)
can trip. The table operations were already offloaded; the three LARGEST
bodies -- table import, reference import, vocabulary fetch -- were not.

Each test swaps the heavy function for a probe that records whether an event
loop was running where it executed. Inside asyncio.to_thread there is none.
"""

from __future__ import annotations

import asyncio

import pandas as pd

HEADERS = {"X-API-Key": "test-key"}
CSV = (
    b"properties.cclom:title;properties.cclom:general_description;"
    b"properties.cclom:general_keyword;properties.ccm:taxonid\nT;D;K;http://x/1\n"
)


def _probe(monkeypatch, module: str, name: str, result):
    """Replace ``module.name`` with a stand-in that records its thread context."""
    seen: dict[str, bool] = {}

    def stand_in(*args, **kwargs):
        try:
            asyncio.get_running_loop()
            seen["on_loop"] = True
        except RuntimeError:
            seen["on_loop"] = False
        return result

    monkeypatch.setattr(f"{module}.{name}", stand_in)
    return seen


def test_table_import_parses_off_the_event_loop(make_client, monkeypatch):
    seen = _probe(monkeypatch, "app.routes.tables", "read_table", pd.DataFrame({"a": ["1"]}))
    res = make_client().post(
        "/refine/datasets/import", files={"file": ("t.csv", b"a\n1\n", "text/csv")}, headers=HEADERS
    )
    assert res.status_code == 200, res.text
    assert seen["on_loop"] is False


def test_reference_import_scrubs_off_the_event_loop(make_client, monkeypatch):
    frame = pd.DataFrame({"properties.cclom:title": ["T"]})
    meta = {"name": "r", "row_count": 1, "text_columns": [], "label_column": "x",
            "pii": {"rows_scanned": 1, "rows_affected": 0, "counts": {}}, "group_counts": {}}
    seen = _probe(monkeypatch, "app.routes.references", "ingest_reference", (frame, meta))
    res = make_client().post("/references/import", files={"file": ("r.csv", CSV, "text/csv")}, headers=HEADERS)
    assert res.status_code == 200, res.text
    assert seen["on_loop"] is False


def test_vocabulary_fetch_runs_off_the_event_loop(make_client, monkeypatch):
    raw = {"@context": {}, "id": "https://x/v", "type": "ConceptScheme", "title": {"de": "V"},
           "hasTopConcept": [{"id": "https://x/v/a", "prefLabel": {"de": "A"}}]}
    seen = _probe(monkeypatch, "app.routes.vocabs", "fetch_json", raw)
    body = {"url": "https://vocabs.openeduhub.de/v/index.json"}
    res = make_client().post("/vocabs/fetch", json=body, headers=HEADERS)
    assert res.status_code == 200, res.text
    assert seen["on_loop"] is False
