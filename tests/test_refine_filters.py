"""Refine part 2: filter operations (drop + transform), the ops-log sidecar,
and the preview-vs-apply route. Encoder faked (no model download)."""

from __future__ import annotations

import json

import pandas as pd

from tests.conftest import FakeEncoder

HEADERS = {"X-API-Key": "test-key"}

TITLE = "properties.cclom:title"
DESC = "properties.cclom:general_description"
KEYW = "properties.cclom:general_keyword"
LABEL = "properties.ccm:taxonid"
COLS = [TITLE, DESC, KEYW]


def _df(rows: list[tuple[str, str, str, str]]) -> pd.DataFrame:
    return pd.DataFrame(rows, columns=[TITLE, DESC, KEYW, LABEL])


def _ctx(**over):
    base = {"text_columns": COLS, "label_column": LABEL, "label_separator": ","}
    base.update(over)
    return base


# ---------------------------------------------------------- drop filters ----


def test_dedupe_exact_drops_duplicate_combined_text():
    from app.refine.filters import run_filter

    df = _df([
        ("Optik", "Licht und Brechung", "kw", "A"),
        ("Optik", "Licht und Brechung", "kw", "A"),   # exact dup
        ("Mechanik", "Kraft", "kw", "B"),
    ])
    new, stats = run_filter("dedupe_exact", df, {}, _ctx())
    assert stats["before"] == 3 and stats["after"] == 2 and stats["removed"] == 1
    assert len(new) == 2


def test_length_corridor_keeps_only_in_range():
    from app.refine.filters import run_filter

    df = _df([
        ("Kurz", "x", "", "A"),                        # combined "Kurz x" = 6
        ("Titel", "Eine mittlere Beschreibung hier", "kw", "A"),
        ("Lang", "y" * 100, "kw", "A"),
    ])
    new, stats = run_filter("length", df, {"min": 10, "max": 60}, _ctx())
    assert stats["after"] == 1
    assert new.iloc[0][TITLE] == "Titel"


def test_drop_no_label_removes_labelless_rows():
    from app.refine.filters import run_filter

    df = _df([("A", "text one", "kw", "A"), ("B", "text two", "kw", "")])
    new, stats = run_filter("drop_no_label", df, {}, _ctx())
    assert stats["after"] == 1 and new.iloc[0][LABEL] == "A"


def test_label_filter_rewrites_labels_and_drops_emptied_rows():
    from app.refine.filters import run_filter

    df = _df([
        ("A", "text one", "kw", "disc/1,other/9"),
        ("B", "text two", "kw", "other/9"),          # loses all labels -> dropped
    ])
    new, stats = run_filter("label_filter", df, {"substring": "disc/"}, _ctx())
    assert stats["after"] == 1
    assert new.iloc[0][LABEL] == "disc/1"  # 'other/9' stripped out


def test_cap_per_label_balances_majority():
    from app.refine.filters import run_filter

    df = _df([(f"t{i}", f"text nummer {i}", "kw", "A") for i in range(5)]
             + [("m1", "text mecha", "kw", "B")])
    new, stats = run_filter("cap_per_label", df, {"cap": 2}, _ctx())
    counts = new[LABEL].str.split(",").explode().str.strip().value_counts().to_dict()
    assert counts["A"] == 2 and counts["B"] == 1
    assert stats["removed"] == 3


def test_dedupe_semantic_uses_encoder_threshold():
    from app.refine.filters import run_filter

    df = _df([
        ("Optik", "Alles ueber Brechung im Alltag", "kw", "A"),
        ("Optik zwei", "Noch mehr zur Brechung erklaert", "kw", "A"),  # same fake direction
        ("Kunst", "Farbenlehre der Klassik", "kw", "B"),
    ])
    new, stats = run_filter("dedupe_semantic", df, {"threshold": 0.9}, _ctx(encoder=FakeEncoder()))
    assert stats["after"] == 2  # one of the two 'Brechung' rows dropped
    assert "Kunst" in set(new[TITLE])


# ----------------------------------------------------- transform filters ----


def test_markup_cleans_text_columns_in_place():
    from app.refine.filters import run_filter

    df = _df([("<b>Titel</b>", "Text mit &amp; Zeichen", "kw", "A")])
    new, stats = run_filter("markup", df, {}, _ctx())
    assert new.iloc[0][TITLE] == "Titel"
    assert new.iloc[0][DESC] == "Text mit & Zeichen"
    assert stats["changed"] == 1
    assert stats["examples"]  # before/after diff surfaced


def test_pii_mask_and_drop_modes():
    from app.refine.filters import run_filter

    df = _df([("Kontakt", "Mail an test@x.de bitte", "kw", "A"),
              ("Sauber", "Ganz ohne Kontakt", "kw", "A")])

    masked, stats = run_filter("pii", df, {"mode": "mask"}, _ctx())
    assert "[email]" in masked.iloc[0][DESC] and "test@x.de" not in masked.iloc[0][DESC]
    assert stats["changed"] == 1

    dropped, stats = run_filter("pii", df, {"mode": "drop"}, _ctx())
    assert stats["after"] == 1 and dropped.iloc[0][TITLE] == "Sauber"


def test_unknown_filter_raises():
    from app.refine.filters import run_filter

    try:
        run_filter("nonsense", _df([("A", "b", "c", "A")]), {}, _ctx())
    except ValueError as exc:
        assert "nonsense" in str(exc)
    else:
        raise AssertionError("expected ValueError")


# ------------------------------------------------------------- ops sidecar ----


def test_ops_log_carries_forward(tmp_path):
    from app.refine.store import append_op, read_ops
    from app.settings import Settings

    settings = Settings(auth_key=None, data_dir=tmp_path / "data")
    assert read_ops(settings, "d1") == []
    append_op(settings, "d1", {"filter": "dedupe_exact", "removed": 3})
    append_op(settings, "d1", {"filter": "length", "removed": 1})
    ops = read_ops(settings, "d1")
    assert [o["filter"] for o in ops] == ["dedupe_exact", "length"]


# ------------------------------------------------------------------ routes ----


def _import(client, df: pd.DataFrame, name: str = "src"):
    payload = df.to_csv(sep=";", index=False).encode("utf-8")
    return client.post("/refine/datasets/import",
                       files={"file": (f"{name}.csv", payload, "text/csv")},
                       data={"name": name}, headers=HEADERS)


def test_filter_preview_writes_nothing_apply_writes_target(make_client, tmp_path, monkeypatch):
    import app.routes.refine as refine_route

    monkeypatch.setattr(refine_route, "_test_encoder", FakeEncoder())
    client = make_client()
    _import(client, _df([
        ("Optik", "Licht und Brechung", "kw", "disc/1"),
        ("Optik", "Licht und Brechung", "kw", "disc/1"),
        ("Mechanik", "Kraft und Hebel", "kw", "disc/2"),
    ]))

    preview = client.post("/refine/src/filter",
                          json={"filter": "dedupe_exact", "params": {},
                                "text_columns": COLS, "label_column": LABEL},
                          headers=HEADERS)
    assert preview.status_code == 200, preview.text
    assert preview.json()["before"] == 3 and preview.json()["after"] == 2
    # Preview persisted nothing.
    assert [d["name"] for d in client.get("/refine/datasets", headers=HEADERS).json()["datasets"]] == ["src"]

    applied = client.post("/refine/src/filter",
                          json={"filter": "dedupe_exact", "params": {}, "target": "src_dedup",
                                "text_columns": COLS, "label_column": LABEL},
                          headers=HEADERS)
    assert applied.status_code == 200, applied.text
    names = {d["name"] for d in client.get("/refine/datasets", headers=HEADERS).json()["datasets"]}
    assert names == {"src", "src_dedup"}
    assert (tmp_path / "data" / "refine" / "src_dedup.ops.json").exists()
    ops = json.loads((tmp_path / "data" / "refine" / "src_dedup.ops.json").read_text("utf-8"))
    assert ops[-1]["filter"] == "dedupe_exact" and ops[-1]["removed"] == 1


def test_ops_history_route_shows_the_applied_chain(make_client):
    client = make_client()
    _import(client, _df([
        ("Optik", "Licht und Brechung", "kw", "disc/1"),
        ("Optik", "Licht und Brechung", "kw", "disc/1"),
        ("Mechanik", "Kraft und Hebel", "kw", "disc/2"),
    ]))
    # A freshly uploaded dataset has no operations yet.
    assert client.get("/refine/src/ops", headers=HEADERS).json() == {"ops": []}

    client.post("/refine/src/filter",
                json={"filter": "dedupe_exact", "params": {}, "target": "src_dedup",
                      "text_columns": COLS, "label_column": LABEL}, headers=HEADERS)
    ops = client.get("/refine/src_dedup/ops", headers=HEADERS).json()["ops"]
    assert [o["filter"] for o in ops] == ["dedupe_exact"]

    assert client.get("/refine/missing/ops", headers=HEADERS).status_code == 404


def test_filter_rejects_bad_target_and_unknown_dataset(make_client):
    client = make_client()
    _import(client, _df([("A", "some text here", "kw", "disc/1")]))
    r = client.post("/refine/src/filter",
                    json={"filter": "dedupe_exact", "params": {}, "target": "../escape",
                          "text_columns": COLS, "label_column": LABEL}, headers=HEADERS)
    assert r.status_code == 400
    r = client.post("/refine/missing/filter",
                    json={"filter": "dedupe_exact", "params": {}, "text_columns": COLS,
                          "label_column": LABEL}, headers=HEADERS)
    assert r.status_code == 404
