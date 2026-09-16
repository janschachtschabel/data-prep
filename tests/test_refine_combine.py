"""Refine part 3: column-mapping assistant + multi-source combine with source
column and conflict resolution (highest-priority source wins on text dupes)."""

from __future__ import annotations

import json

import pandas as pd

HEADERS = {"X-API-Key": "test-key"}

TITLE = "properties.cclom:title"
DESC = "properties.cclom:general_description"
KEYW = "properties.cclom:general_keyword"
LABEL = "properties.ccm:taxonid"
TARGET = [TITLE, DESC, KEYW, LABEL]


# ------------------------------------------------------ mapping assistant ----


def test_suggest_mapping_matches_exact_leaf_and_reports_unmatched():
    from app.refine.combine import suggest_mapping

    source_cols = ["title", "general_description", "cclom:general_keyword", "ccm:taxonid", "foo"]
    result = suggest_mapping(source_cols, TARGET)
    assert result["mapping"] == {
        TITLE: "title",
        DESC: "general_description",
        KEYW: "cclom:general_keyword",
        LABEL: "ccm:taxonid",
    }
    assert result["unmatched_targets"] == []
    assert "foo" in result["unused_sources"]


def test_suggest_mapping_leaves_unmatched_target_none():
    from app.refine.combine import suggest_mapping

    result = suggest_mapping(["title", "irrelevant"], TARGET)
    assert result["mapping"][TITLE] == "title"
    assert result["mapping"][DESC] is None
    assert DESC in result["unmatched_targets"]


# --------------------------------------------------------------- combine ----


def test_combine_maps_adds_source_and_resolves_conflicts():
    from app.refine.combine import combine_datasets

    curated = pd.DataFrame(
        [["Optik", "Licht und Brechung", "kw", "disc/1"]],
        columns=["title", "beschr", "kw", "lab"],
    )
    synthetic = pd.DataFrame(
        [
            ["Optik", "Licht und Brechung", "kw", "disc/1"],   # dup of curated -> curated wins
            ["Neu", "Ganz anderer Text", "kw", "disc/2"],
        ],
        columns=[TITLE, DESC, KEYW, LABEL],
    )
    sources = [
        {"df": curated, "label": "curated",
         "mapping": {TITLE: "title", DESC: "beschr", KEYW: "kw", LABEL: "lab"}},
        {"df": synthetic, "label": "synthetic",
         "mapping": {TITLE: TITLE, DESC: DESC, KEYW: KEYW, LABEL: LABEL}},
    ]
    combined, stats = combine_datasets(sources, TARGET, text_columns=[TITLE, DESC, KEYW])

    assert list(combined.columns) == [*TARGET, "source"]
    assert len(combined) == 2  # 3 rows in, 1 conflict removed
    # The surviving Optik row carries the curated provenance.
    optik = combined[combined[TITLE] == "Optik"].iloc[0]
    assert optik["source"] == "curated"
    assert set(combined["source"]) == {"curated", "synthetic"}

    assert stats["total_in"] == 3
    assert stats["total_out"] == 2
    assert stats["conflicts_resolved"] == 1
    by_label = {s["label"]: s for s in stats["per_source"]}
    assert by_label["curated"]["rows_kept"] == 1
    assert by_label["synthetic"]["rows_in"] == 2 and by_label["synthetic"]["rows_kept"] == 1


def test_combine_fills_unmapped_targets_with_empty():
    from app.refine.combine import combine_datasets

    df = pd.DataFrame([["nur Titel", "disc/1"]], columns=["title", "lab"])
    sources = [{"df": df, "label": "src", "mapping": {TITLE: "title", LABEL: "lab"}}]
    combined, _ = combine_datasets(sources, TARGET, text_columns=[TITLE, DESC, KEYW])
    assert combined.iloc[0][DESC] == "" and combined.iloc[0][KEYW] == ""
    assert combined.iloc[0][TITLE] == "nur Titel"


def test_combine_keeps_the_marks_that_say_where_a_row_came_from():
    """A balanced dataset combined with another lost `generated_for` — the mapping
    only carries target columns — and its generated rows could then reach a holdout
    as if they were real (review #14)."""
    from app.refine.combine import combine_datasets

    balanced = pd.DataFrame(
        [["Echt", "Text eins.", "k", "disc/1", "", "disc/1"],
         ["Erzeugt", "Text zwei.", "k", "disc/1", "disc/1", ""]],
        columns=[TITLE, DESC, KEYW, LABEL, "generated_for", "example_for"])
    plain = pd.DataFrame([["Anders", "Text drei.", "k", "disc/2"]], columns=TARGET)
    identity = {c: c for c in TARGET}
    sources = [{"df": balanced, "label": "a", "mapping": identity},
               {"df": plain, "label": "b", "mapping": identity}]

    combined, _ = combine_datasets(sources, TARGET, text_columns=[TITLE, DESC, KEYW])

    by_title = combined.set_index(TITLE)
    assert by_title.loc["Erzeugt", "generated_for"] == "disc/1"
    assert by_title.loc["Echt", "example_for"] == "disc/1"
    assert by_title.loc["Anders", "generated_for"] == ""


def test_combine_adds_no_mark_columns_when_no_source_has_any():
    from app.refine.combine import combine_datasets

    df = pd.DataFrame([["T", "D", "K", "disc/1"]], columns=TARGET)
    sources = [{"df": df, "label": "a", "mapping": {c: c for c in TARGET}}]

    combined, _ = combine_datasets(sources, TARGET, text_columns=[TITLE, DESC, KEYW])

    assert list(combined.columns) == [*TARGET, "source"]


def test_combine_rejects_text_columns_outside_target():
    """text_columns must be a subset of target_columns; otherwise the combined
    frame lacks that column and the old code raised a bare KeyError -> HTTP 500.
    Now it is a client-safe ValueError the route maps to 400."""
    import pytest

    from app.refine.combine import combine_datasets

    df = pd.DataFrame([["Titel", "disc/1"]], columns=["title", "lab"])
    sources = [{"df": df, "label": "src", "mapping": {TITLE: "title", LABEL: "lab"}}]
    with pytest.raises(ValueError, match="target"):
        combine_datasets(sources, TARGET, text_columns=[TITLE, "properties.not:in_target"])


def test_combine_rejects_empty_text_columns():
    """An empty text_columns list indexes text_columns[0] when building the
    dedupe key (IndexError -> HTTP 500). Reject it up front with a client-safe
    ValueError the route maps to 400."""
    import pytest

    from app.refine.combine import combine_datasets

    df = pd.DataFrame([["Titel", "disc/1"]], columns=["title", "lab"])
    sources = [{"df": df, "label": "src", "mapping": {TITLE: "title", LABEL: "lab"}}]
    with pytest.raises(ValueError, match="text_columns"):
        combine_datasets(sources, TARGET, text_columns=[])


# ------------------------------------------------------------------ routes ----


def _import(client, df: pd.DataFrame, name: str):
    payload = df.to_csv(sep=";", index=False).encode("utf-8")
    return client.post("/refine/datasets/import",
                       files={"file": (f"{name}.csv", payload, "text/csv")},
                       data={"name": name}, headers=HEADERS)


def test_suggest_route_returns_mapping_per_source(make_client):
    client = make_client()
    _import(client, pd.DataFrame([["a", "b", "c", "disc/1"]],
                                 columns=["title", "general_description", "general_keyword", "taxonid"]),
            "raw")
    r = client.post("/refine/combine/suggest", json={"sources": ["raw"]}, headers=HEADERS)
    assert r.status_code == 200, r.text
    mapping = r.json()["raw"]["mapping"]
    assert mapping[TITLE] == "title" and mapping[LABEL] == "taxonid"


def test_combine_route_writes_target_with_ops_log(make_client, tmp_path):
    client = make_client()
    _import(client, pd.DataFrame([[TITLE and "Optik", "Licht", "kw", "disc/1"]],
                                 columns=[TITLE, DESC, KEYW, LABEL]), "a")
    _import(client, pd.DataFrame([["Optik", "Licht", "kw", "disc/1"], ["Neu", "Text", "kw", "disc/2"]],
                                 columns=[TITLE, DESC, KEYW, LABEL]), "b")

    ident = {TITLE: TITLE, DESC: DESC, KEYW: KEYW, LABEL: LABEL}
    r = client.post("/refine/combine", json={
        "sources": [{"name": "a", "label": "curated", "mapping": ident},
                    {"name": "b", "label": "synthetic", "mapping": ident}],
        "target": "merged",
    }, headers=HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["total_out"] == 2 and body["conflicts_resolved"] == 1

    names = {d["name"] for d in client.get("/refine/datasets", headers=HEADERS).json()["datasets"]}
    assert "merged" in names
    ops = json.loads((tmp_path / "data" / "refine" / "merged.ops.json").read_text("utf-8"))
    assert ops[-1]["op"] == "combine"
    assert ops[-1]["sources"] == ["a", "b"]


def test_combine_route_rejects_unknown_source_and_bad_target(make_client):
    client = make_client()
    _import(client, pd.DataFrame([["Optik", "Licht", "kw", "disc/1"]],
                                 columns=[TITLE, DESC, KEYW, LABEL]), "a")
    ident = {TITLE: TITLE, DESC: DESC, KEYW: KEYW, LABEL: LABEL}
    r = client.post("/refine/combine", json={
        "sources": [{"name": "missing", "label": "x", "mapping": ident}], "target": "out",
    }, headers=HEADERS)
    assert r.status_code == 404
    r = client.post("/refine/combine", json={
        "sources": [{"name": "a", "label": "x", "mapping": ident}], "target": "../escape",
    }, headers=HEADERS)
    assert r.status_code == 400
