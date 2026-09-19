"""Refine part 4: stratified text-disjoint holdout split, balance report, and
the guarded api_v3 push for refine datasets (transport mocked)."""

from __future__ import annotations

import json

import httpx
import pandas as pd
import yaml

HEADERS = {"X-API-Key": "test-key"}

TITLE = "properties.cclom:title"
DESC = "properties.cclom:general_description"
KEYW = "properties.cclom:general_keyword"
LABEL = "properties.ccm:taxonid"
COLS = [TITLE, DESC, KEYW]


def _dataset() -> pd.DataFrame:
    # 4 labels, each with several DISTINCT texts, so a stratified split can put
    # some of every label on each side.
    rows = []
    for lab in ("disc/A", "disc/B", "disc/C", "disc/D"):
        for i in range(8):
            rows.append([f"Titel {lab}-{i}", f"Ein eindeutiger Beispieltext {lab} nummer {i}", "kw", lab])
    return pd.DataFrame(rows, columns=[TITLE, DESC, KEYW, LABEL])


def _combined(df):
    from app.textnorm import clean_text
    return {clean_text(f"{t} {d} {k}") for t, d, k in zip(df[TITLE], df[DESC], df[KEYW], strict=False)}


# ------------------------------------------------------------- split logic ----


def test_holdout_split_is_partition_and_text_disjoint():
    from app.refine.prep import holdout_split

    df = _dataset()
    train, holdout, stats = holdout_split(df, COLS, LABEL, holdout_fraction=0.25, seed=1)
    assert len(train) + len(holdout) == len(df)
    assert not (_combined(train) & _combined(holdout))  # no shared text across sides
    assert len(holdout) > 0
    assert stats["train_rows"] == len(train) and stats["holdout_rows"] == len(holdout)


def test_holdout_split_represents_every_label_on_both_sides():
    from app.refine.prep import holdout_split

    df = _dataset()
    train, holdout, _ = holdout_split(df, COLS, LABEL, holdout_fraction=0.25, seed=1)
    for side in (train, holdout):
        assert set(side[LABEL]) == {"disc/A", "disc/B", "disc/C", "disc/D"}


def test_holdout_split_is_deterministic():
    from app.refine.prep import holdout_split

    df = _dataset()
    a = holdout_split(df, COLS, LABEL, holdout_fraction=0.3, seed=7)[1]
    b = holdout_split(df, COLS, LABEL, holdout_fraction=0.3, seed=7)[1]
    assert list(a[TITLE]) == list(b[TITLE])


def test_a_generated_row_never_lands_in_the_holdout():
    """The honesty of every later metric rests on this. A holdout containing rows an
    LLM wrote from the same examples the model trained on measures how well the model
    learned that LLM, and the F1 flatters itself by a margin nobody can see."""
    from app.refine.prep import holdout_split

    df = _dataset()
    generated = df.head(10).copy()
    generated[TITLE] = [f"Erzeugt {i}" for i in range(10)]
    generated[DESC] = [f"Ein erzeugter Text nummer {i}" for i in range(10)]
    generated["generated_for"] = generated[LABEL]
    df["generated_for"] = ""
    both = pd.concat([df, generated], ignore_index=True)

    train, holdout, stats = holdout_split(both, COLS, LABEL, holdout_fraction=0.25, seed=1)

    assert len(holdout) > 0, "the real rows still form a holdout"
    assert set(holdout["generated_for"]) == {""}
    assert (train["generated_for"] != "").sum() == 10, "they are training rows, not lost"
    assert stats["generated_excluded"] == 10
    assert len(train) + len(holdout) == len(both)


def test_a_dataset_without_the_provenance_column_splits_exactly_as_before():
    """Balancing is optional. A dataset that never went through it must not change
    behaviour because the split learned a new column."""
    from app.refine.prep import holdout_split

    df = _dataset()
    train, holdout, stats = holdout_split(df, COLS, LABEL, holdout_fraction=0.25, seed=1)

    assert len(train) + len(holdout) == len(df)
    assert stats["generated_excluded"] == 0


def test_a_row_the_generator_imitated_never_lands_in_the_holdout():
    """Generated rows are paraphrases of the real rows shown to the generator. If
    such an example sits in the holdout, its paraphrases sit in training, and the
    holdout measures recall of text the model has effectively seen (review #2)."""
    from app.refine.prep import holdout_split

    df = _dataset()
    df["generated_for"] = ""
    df["example_for"] = ""
    df.loc[df[LABEL] == "disc/A", "example_for"] = "disc/A"   # all 8 A rows were examples

    train, holdout, stats = holdout_split(df, COLS, LABEL, holdout_fraction=0.25, seed=1)

    assert set(holdout["example_for"]) == {""}
    assert (train["example_for"] != "").sum() == 8
    assert stats["real_kept_in_train"] == 8
    assert len(train) + len(holdout) == len(df)


def test_a_row_an_llm_completed_never_lands_in_the_holdout():
    """Enrichment writes into REAL rows — keywords, a description. Evaluated on, such a
    row measures the model on text the LLM wrote, which is the flattering number the
    marks exist to keep out."""
    from app.refine.prep import holdout_split

    df = _dataset()
    df["enriched_fields"] = ""
    df.loc[df[LABEL] == "disc/B", "enriched_fields"] = KEYW   # all 8 B rows were completed

    train, holdout, stats = holdout_split(df, COLS, LABEL, holdout_fraction=0.25, seed=1)

    assert set(holdout["enriched_fields"]) == {""}
    assert (train["enriched_fields"] != "").sum() == 8
    assert stats["real_kept_in_train"] == 8
    assert stats["labels_without_holdout"] == ["disc/B"]


def test_a_real_row_sharing_text_with_a_generated_one_is_kept_and_reported():
    """The whole text group stays in training to keep the split text-disjoint. That
    costs the holdout a real row, and the stats have to say so (review #17)."""
    from app.refine.prep import holdout_split

    df = _dataset()
    df["generated_for"] = ""
    twin = df.iloc[[0]].copy()
    twin["generated_for"] = twin[LABEL]
    both = pd.concat([df, twin], ignore_index=True)

    train, holdout, stats = holdout_split(both, COLS, LABEL, holdout_fraction=0.25, seed=1)

    text = both.iloc[0][TITLE]
    assert text not in set(holdout[TITLE]), "the real twin must stay with its copy"
    assert stats["real_kept_in_train"] == 1
    assert stats["generated_excluded"] == 1


def test_a_missing_mark_is_not_a_mark():
    """pandas hands an in-memory caller NaN for a missing cell. `str(nan)` is "nan",
    which is not blank — every real row was treated as generated and the holdout came
    out empty (review #16)."""
    from app.refine.prep import holdout_split

    df = _dataset()
    df["generated_for"] = pd.Series([float("nan")] * len(df), dtype=object)
    df.loc[0, "generated_for"] = df.loc[0, LABEL]

    train, holdout, stats = holdout_split(df, COLS, LABEL, holdout_fraction=0.25, seed=1)

    assert stats["generated_excluded"] == 1
    assert len(holdout) > 0


def test_the_split_does_not_depend_on_a_unique_index():
    """The holdout was selected by index LABEL. With a repeated label, a generated row
    sharing it with a holdout row went into the holdout too — while the stats still
    said it had been excluded (review #17)."""
    from app.refine.prep import holdout_split

    df = _dataset()
    df["generated_for"] = ""
    generated = df.copy()
    generated[TITLE] = [f"Erzeugt {i}" for i in range(len(generated))]
    generated[DESC] = [f"Ein erzeugter Text nummer {i}" for i in range(len(generated))]
    generated["generated_for"] = generated[LABEL]
    both = pd.concat([df, generated])          # index 0..31 twice
    assert not both.index.is_unique

    train, holdout, stats = holdout_split(both, COLS, LABEL, holdout_fraction=0.25, seed=1)

    assert len(holdout) > 0
    assert set(holdout["generated_for"]) == {""}
    assert len(train) + len(holdout) == len(both)


def test_the_split_names_the_labels_whose_holdout_the_marks_emptied():
    """A label whose every real row served as an example keeps them all in training
    and has no holdout at all. `real_kept_in_train` is one total across labels; which
    labels became unevaluable has to be said by name (round 2, finding 7)."""
    from app.refine.prep import holdout_split

    df = _dataset()
    df["generated_for"] = ""
    df["example_for"] = ""
    df.loc[df[LABEL] == "disc/A", "example_for"] = "disc/A"

    _, holdout, stats = holdout_split(df, COLS, LABEL, holdout_fraction=0.25, seed=1)

    assert stats["labels_without_holdout"] == ["disc/A"]
    assert "disc/A" not in set(holdout[LABEL])


def test_an_unmarked_split_names_no_label():
    from app.refine.prep import holdout_split

    _, _, stats = holdout_split(_dataset(), COLS, LABEL, holdout_fraction=0.25, seed=1)

    assert stats["labels_without_holdout"] == []


def test_balance_report_counts_and_recommends_min_samples():
    from app.refine.prep import balance_report

    df = _dataset()
    report = balance_report(df, COLS, LABEL)
    assert report["label_support"] == {"disc/A": 8, "disc/B": 8, "disc/C": 8, "disc/D": 8}
    assert report["recommended_min_samples"] >= 1
    assert report["labels_below_recommended"] == {}


# ------------------------------------------------------------- split route ----


def _import(client, df: pd.DataFrame, name: str = "src"):
    payload = df.to_csv(sep=";", index=False).encode("utf-8")
    return client.post("/refine/datasets/import",
                       files={"file": (f"{name}.csv", payload, "text/csv")},
                       data={"name": name}, headers=HEADERS)


def test_split_route_writes_train_and_holdout(make_client, tmp_path):
    client = make_client()
    _import(client, _dataset())
    r = client.post("/refine/src/split",
                    json={"text_columns": COLS, "label_column": LABEL,
                          "holdout_fraction": 0.25, "target": "prepared"},
                    headers=HEADERS)
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["train_rows"] + body["holdout_rows"] == 32
    assert "balance" in body

    names = {d["name"] for d in client.get("/refine/datasets", headers=HEADERS).json()["datasets"]}
    assert {"prepared_train", "prepared_holdout"} <= names
    ops = json.loads((tmp_path / "data" / "refine" / "prepared_train.ops.json").read_text("utf-8"))
    assert ops[-1]["op"] == "holdout_split"


def test_split_accepts_a_target_at_the_field_limit(make_client):
    """A 100-character target is valid; the names split derives from it are
    108 characters and must be too."""
    client = make_client()
    _import(client, _dataset())
    r = client.post("/refine/src/split",
                    json={"text_columns": COLS, "label_column": LABEL,
                          "holdout_fraction": 0.25, "target": "t" * 100},
                    headers=HEADERS)
    assert r.status_code == 200, r.text


def test_split_checks_the_derived_names_before_writing_anything(make_client, tmp_path):
    """A target whose "_holdout" form overflows the bound must be refused up
    front -- not after "_train" has been written, which left half a split."""
    client = make_client()
    _import(client, _dataset())
    target = "\u00e4" * 97  # 194 bytes; "_holdout" makes it 202
    r = client.post("/refine/src/split",
                    json={"text_columns": COLS, "label_column": LABEL,
                          "holdout_fraction": 0.25, "target": target},
                    headers=HEADERS)
    assert r.status_code == 400
    assert not list((tmp_path / "data" / "refine").glob(f"{target}*"))


def test_a_dataset_with_a_long_legacy_name_stays_reachable(make_client, tmp_path):
    """Names up to 108 characters were written by splits before the bound
    existed; they must still load and delete."""
    client = make_client()
    _import(client, _dataset())
    legacy = "L" * 105
    refine = tmp_path / "data" / "refine"
    (refine / f"{legacy}.csv").write_bytes((refine / "src.csv").read_bytes())
    assert client.get(f"/refine/{legacy}/rows", headers=HEADERS).status_code == 200
    assert client.delete(f"/refine/{legacy}", headers=HEADERS).status_code == 200


def test_every_field_that_names_a_dataset_accepts_what_the_store_allows(make_client, tmp_path):
    """A 105-character name loaded and deleted, but the body fields that NAME a
    dataset still capped at 100 characters: it could not be worked on in place,
    joined as the right side or combined (422, review finding)."""
    client = make_client()
    _import(client, _dataset())
    legacy = "L" * 105
    refine = tmp_path / "data" / "refine"
    (refine / f"{legacy}.csv").write_bytes((refine / "src.csv").read_bytes())
    rule = {"op": "rules", "params": {"rules": [{"column": LABEL, "op": "ne", "value": "zzz"}]}}

    r = client.post(f"/refine/{legacy}/op", json={**rule, "target": legacy}, headers=HEADERS)
    assert r.status_code == 200, r.text
    r = client.post("/refine/src/join", headers=HEADERS,
                    json={"right": legacy, "keys": [{"left": LABEL, "right": LABEL}]})
    assert r.status_code == 200, r.text
    mapping = {col: col for col in [*COLS, LABEL]}
    r = client.post("/refine/combine", headers=HEADERS, json={
        "sources": [{"name": legacy, "label": "L", "mapping": mapping}], "target": "c" * 105,
        "target_columns": [*COLS, LABEL], "text_columns": COLS})
    assert r.status_code == 200, r.text


# -------------------------------------------------------------- push route ----


def _client_with_push(make_client, tmp_path, monkeypatch, url: str):
    (tmp_path / "config.yaml").write_text(yaml.safe_dump({
        "api_v3": {"url": url, "api_key_env": "TEST_APIV3_KEY"},
    }), encoding="utf-8")
    monkeypatch.setenv("DATAPREP_CONFIG_FILE", str(tmp_path / "config.yaml"))
    monkeypatch.setenv("TEST_APIV3_KEY", "apiv3-secret")
    client = make_client()
    _import(client, _dataset(), "src")
    return client


def test_push_route_uploads_refine_dataset(make_client, tmp_path, monkeypatch):
    import app.apiv3 as apiv3

    client = _client_with_push(make_client, tmp_path, monkeypatch, "http://127.0.0.1:8021")
    captured: dict = {}

    def handler(request: httpx.Request) -> httpx.Response:
        captured["url"] = str(request.url)
        captured["key"] = request.headers.get("X-API-Key")
        captured["body"] = request.content
        return httpx.Response(200, json={"status": "imported"})

    monkeypatch.setattr(apiv3, "_test_transport", httpx.MockTransport(handler))
    r = client.post("/refine/src/push", headers=HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["api_v3"] == {"status": "imported"}
    assert captured["url"].endswith("/datasets/import")
    assert captured["key"] == "apiv3-secret"
    assert b"disc/A" in captured["body"]


def test_push_of_a_name_api_v3_already_has_is_a_conflict_not_a_gateway_error(
    make_client, tmp_path, monkeypatch
):
    """api_v3 refuses an existing dataset name with 409. Relayed as 502
    "rejected the upload", the operator could not tell a taken name from a
    broken server; it is the same conflict here, with what to do about it."""
    import app.apiv3 as apiv3

    client = _client_with_push(make_client, tmp_path, monkeypatch, "http://127.0.0.1:8021")
    monkeypatch.setattr(apiv3, "_test_transport", httpx.MockTransport(
        lambda request: httpx.Response(409, json={"detail": "Dataset 'src.csv' already exists."})))
    r = client.post("/refine/src/push", headers=HEADERS)
    assert r.status_code == 409
    assert "src.csv" in r.json()["detail"]
    assert "already" in r.json()["detail"]


def test_a_push_api_v3_refuses_says_why(make_client, tmp_path, monkeypatch):
    """api_v3 caps a dataset name at 100 characters including ".csv"; a longer
    one came back as a bare "rejected the upload (HTTP 400)" (review finding).
    api_v3 is the operator's own configured host, so its reason is relayed."""
    import app.apiv3 as apiv3

    client = _client_with_push(make_client, tmp_path, monkeypatch, "http://127.0.0.1:8021")
    monkeypatch.setattr(apiv3, "_test_transport", httpx.MockTransport(
        lambda request: httpx.Response(400, json={"detail": "Invalid dataset name: too long."})))
    r = client.post("/refine/src/push", headers=HEADERS)
    assert r.status_code == 502
    assert "Invalid dataset name: too long." in r.json()["detail"]


def test_push_route_rejects_foreign_host(make_client, tmp_path, monkeypatch):
    client = _client_with_push(make_client, tmp_path, monkeypatch, "https://evil.example.org")
    r = client.post("/refine/src/push", headers=HEADERS)
    assert r.status_code == 400
    assert "not allowed" in r.json()["detail"]
