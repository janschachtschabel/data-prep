"""Refine part 1: text normalization parity with api_v3, dataset store,
distribution analysis and the training preflight (effective-count simulation)."""

from __future__ import annotations

HEADERS = {"X-API-Key": "test-key"}

TITLE = "properties.cclom:title"
DESC = "properties.cclom:general_description"
KEYW = "properties.cclom:general_keyword"
LABEL = "properties.ccm:taxonid"
CSV_HEADER = f"{TITLE};{DESC};{KEYW};{LABEL}\n"


# ------------------------------------------------- text normalization ----


def test_clean_text_matches_api_v3_rules():
    from app.textnorm import clean_text

    assert clean_text("<p>Hallo &amp; Welt</p>") == "Hallo & Welt"
    assert clean_text("[Link](http://x.de) bleibt") == "Link bleibt"
    assert clean_text("**fett** _kursiv_ `code`") == "fett kursiv code"
    assert clean_text("viel   \n  Raum") == "viel Raum"
    assert clean_text(None) == ""


def test_clean_text_matches_api_v3_cleaning_version_2():
    """api_v3 trains new models with cleaning version 2 (its audit of 2026-09-30, T09): a tag
    starts with `<` and a letter, `/`, `!` or `?`, so a bare `<` ... `>` pair is prose. Its
    patterns also stopped scanning across their own opening delimiter (2026-09-20), which
    keeps a stray `<` or `[` in front of real markup. The expected values are api_v3's own."""
    from app.textnorm import clean_text

    assert clean_text("x < y gilt: Wenn a > b") == "x < y gilt: Wenn a b"
    assert clean_text("Preis &lt; 5 Euro <br> Menge &gt; 3 Stück") == "Preis < 5 Euro Menge 3 Stück"
    assert clean_text("a < b <i>c</i>") == "a < b c"
    assert clean_text("[a [b](c)") == "[a b"
    assert clean_text("[t](http://x.de/a(b)) Rest") == "[t](http://x.de/a(b)) Rest"


def test_split_labels_respects_separator():
    from app.textnorm import split_labels

    assert split_labels("a, b ,c", ",") == ["a", "b", "c"]
    assert split_labels("", ",") == []
    assert split_labels(None, ",") == []


# ------------------------------------------------------------ preflight ----


def _preflight_csv() -> bytes:
    good = "Ein ausreichend langer Beispieltext zum Thema"
    rows = [
        f"Titel A;{good} eins;kw;subject/A",
        f"Titel B;{good} zwei;kw;subject/A",
        f"Titel C;{good} drei;kw;subject/A",
        f"Titel D;{good} vier;kw;subject/B",     # B is rare
        ";a;;subject/A",                          # combined cleaned text "a" (<5) -> dropped
        f"Titel A;{good} eins;kw;subject/A",      # exact duplicate of row 1 -> dropped
        f"Titel E;{good} fuenf;kw;",              # no label -> dropped
    ]
    return (CSV_HEADER + "\n".join(rows) + "\n").encode("utf-8")


def test_preflight_effective_count_and_breakdown():
    from app.refine.analyze import training_preflight
    from app.refine.store import read_csv

    df = read_csv(_preflight_csv())
    report = training_preflight(
        df, [TITLE, DESC, KEYW], LABEL,
        label_separator=",", label_filter="subject/",
        min_text_length=5, drop_duplicates=True, min_samples=3,
    )
    assert report["raw_rows"] == 7
    assert report["kept_after_cleaning"] == 4  # A,A,A,B survive short/dup/no-label filters
    assert report["min_samples"] == 3
    # Only A reaches 3 occurrences; B (1) is dropped, and its row loses its only
    # label -> effective training rows = 3.
    assert report["effective_rows"] == 3
    assert report["learnable_labels"] == 1
    assert report["dropped"]["too_short"] == 1
    assert report["dropped"]["duplicate"] == 1
    assert report["dropped"]["no_label"] == 1
    assert report["per_label_effective"] == {"subject/A": 3}


def test_preflight_auto_min_samples_used_when_unspecified():
    from app.refine.analyze import training_preflight
    from app.refine.store import read_csv

    df = read_csv(_preflight_csv())
    report = training_preflight(df, [TITLE, DESC, KEYW], LABEL, label_separator=",")
    assert report["min_samples"] == 2  # auto for < 1000 rows


# ------------------------------------------------------------- analyze ----


def _analyze_csv() -> bytes:
    rows = [
        "Optik;Licht und Brechung, Mail kontakt@schule.de;Physik;subject/A",
        "Mechanik;Kraft und Hebel;Physik;subject/A",
        "Kunst;Farbenlehre der Klassik;Kunst;subject/B",
        "Optik;Licht und Brechung, Mail kontakt@schule.de;Physik;subject/A",  # exact dup
        ";;;subject/C",  # empty text
    ]
    return (CSV_HEADER + "\n".join(rows) + "\n").encode("utf-8")


def test_analyze_reports_distribution_dupes_and_pii():
    from app.refine.analyze import analyze
    from app.refine.store import read_csv

    df = read_csv(_analyze_csv())
    report = analyze(df, [TITLE, DESC, KEYW], LABEL, label_separator=",")

    assert report["total_rows"] == 5
    assert report["label_support"]["subject/A"] == 3
    assert report["empty_text_rows"] == 1
    assert report["exact_duplicate_rows"] == 1
    assert report["text_length"]["max"] > 0
    assert report["pii"]["counts"]["email"] == 2  # both A-rows carry the address
    assert report["pii"]["rows_affected"] == 2


# -------------------------------------------------------------- routes ----


def _import(client, payload: bytes, name: str = "curated"):
    return client.post(
        "/refine/datasets/import",
        files={"file": (f"{name}.csv", payload, "text/csv")},
        data={"name": name},
        headers=HEADERS,
    )


def test_refine_routes_require_auth(make_client):
    client = make_client()
    assert client.get("/refine/datasets").status_code == 401


def test_store_roundtrip_and_analyze_route(make_client, tmp_path):
    client = make_client()

    r = _import(client, _analyze_csv())
    assert r.status_code == 200, r.text
    assert r.json()["rows"] == 5
    assert set(r.json()["columns"]) == {TITLE, DESC, KEYW, LABEL}

    listed = client.get("/refine/datasets", headers=HEADERS).json()["datasets"]
    assert [d["name"] for d in listed] == ["curated"]

    r = client.post("/refine/curated/analyze",
                    json={"text_columns": [TITLE, DESC, KEYW], "label_column": LABEL},
                    headers=HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["exact_duplicate_rows"] == 1

    r = client.post("/refine/curated/preflight",
                    json={"text_columns": [TITLE, DESC, KEYW], "label_column": LABEL,
                          "min_samples": 2},
                    headers=HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["effective_rows"] >= 1

    # Stored file is the raw upload (refine does not scrub on import).
    stored = (tmp_path / "data" / "refine" / "curated.csv").read_text("utf-8")
    assert "kontakt@schule.de" in stored

    assert client.delete("/refine/curated", headers=HEADERS).json() == {"deleted": "curated"}
    assert client.post("/refine/curated/analyze",
                       json={"text_columns": [TITLE], "label_column": LABEL},
                       headers=HEADERS).status_code == 404


def test_list_datasets_counts_data_rows_not_physical_lines(tmp_path):
    """A cell containing a newline is quoted across two physical lines on disk.
    The listing must count DATA rows (via the CSV parser), not raw lines."""
    import pandas as pd

    from app.refine.store import list_datasets, save_dataset
    from app.settings import Settings

    settings = Settings(auth_key=None, data_dir=tmp_path / "data")
    df = pd.DataFrame({"text": ["line one\nline two", "single"], "label": ["a", "b"]})
    save_dataset(settings, "withnl", df)

    listed = {d["name"]: d for d in list_datasets(settings)}
    assert listed["withnl"]["rows"] == 2  # two rows, not three physical lines
    assert listed["withnl"]["columns"] == ["text", "label"]


def test_analyze_rejects_unknown_columns(make_client):
    client = make_client()
    _import(client, _analyze_csv())
    r = client.post("/refine/curated/analyze",
                    json={"text_columns": ["nope"], "label_column": LABEL}, headers=HEADERS)
    assert r.status_code == 400
    assert "nope" in r.json()["detail"]


def test_import_rejects_bad_name_and_payload(make_client):
    client = make_client()
    r = _import(client, b"not a csv at all\x00\xff", name="../escape")
    assert r.status_code == 400
    r = client.post("/refine/datasets/import",
                    files={"file": ("x.csv", b"", "text/csv")},
                    data={"name": "empty"}, headers=HEADERS)
    assert r.status_code == 400
