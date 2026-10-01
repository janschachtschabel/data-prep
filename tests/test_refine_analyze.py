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
        # C gives A the rows WITHOUT it that api_v3 asks for (T01), and A gives C its own.
        f"Titel F;{good} sechs;kw;subject/C",
        f"Titel G;{good} sieben;kw;subject/C",
        f"Titel H;{good} acht;kw;subject/C",
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
    assert report["raw_rows"] == 10
    assert report["kept_after_cleaning"] == 7  # A,A,A,B,C,C,C survive short/dup/no-label filters
    assert report["min_samples"] == 3
    # A and C reach 3 occurrences with 4 rows without each; B (1) is dropped, and its row
    # loses its only label -> effective training rows = 6 (api_v3's prepare_targets agrees).
    assert report["effective_rows"] == 6
    assert report["learnable_labels"] == 2
    assert report["dropped"]["too_short"] == 1
    assert report["dropped"]["duplicate"] == 1
    assert report["dropped"]["no_label"] == 1
    assert report["per_label_effective"] == {"subject/A": 3, "subject/C": 3}
    assert report["labels_below_min"] == {"subject/B": 1}


def test_preflight_drops_container_labels_like_api_v3():
    """A value ending in `/` names a vocabulary, not a concept, and api_v3 drops it before it
    trains (since 2026-09-08): a row whose only label it is has no label there. The preflight
    drops it the same way. ``textnorm.split_labels`` keeps it, because data-prep rewrites
    label cells and pairs them with display names by position."""
    from app.refine.analyze import training_preflight
    from app.refine.store import read_csv

    root = "http://w3id.org/openeduhub/vocabs/discipline/"
    good = "Ein ausreichend langer Beispieltext zum Thema"
    rows = [f"Titel {i};{good} {i};kw;{root}120" for i in range(3)]
    rows += [f"Titel X;{good} x;kw;{root}", f"Titel Y;{good} y;kw;{root},{root}120"]
    # Two rows of another concept, so 120 is not on every kept row (which api_v3 drops, T01).
    rows += [f"Titel Z{i};{good} z{i};kw;{root}130" for i in range(2)]
    df = read_csv((CSV_HEADER + "\n".join(rows) + "\n").encode("utf-8"))
    report = training_preflight(df, [TITLE, DESC, KEYW], LABEL, label_separator=",", min_samples=2)

    assert report["dropped"]["no_label"] == 1
    assert report["per_label_effective"] == {f"{root}120": 4, f"{root}130": 2}
    assert root not in report["labels_below_min"]


def test_preflight_dedupes_the_way_api_v3s_vectorizer_sees_text():
    """api_v3 compares cleaned texts as its vectorizer sees them -- lower-cased, accents
    stripped (its audit of 2026-09-30, T07): "BRUCHRECHNUNG FÜR EINSTEIGER" beside
    "Bruchrechnung für Einsteiger" is one row there. Compared exactly, the preflight kept both.
    The key equalities are api_v3's own answers."""
    from app.refine.analyze import training_preflight
    from app.refine.store import read_csv
    from app.textnorm import dedupe_key

    assert dedupe_key("Bruchrechnung für Einsteiger") == dedupe_key("BRUCHRECHNUNG FÜR EINSTEIGER")
    assert dedupe_key("Café") == dedupe_key("cafe")
    assert dedupe_key("Bruchrechnung") != dedupe_key("Brüche")

    rows = ["Bruchrechnung für Einsteiger;Brüche addieren und kürzen;kw;subject/A",
            "BRUCHRECHNUNG FÜR EINSTEIGER;BRÜCHE ADDIEREN UND KÜRZEN;KW;subject/A",
            "Geometrie;Flächen und Winkel berechnen;kw;subject/A"]
    df = read_csv((CSV_HEADER + "\n".join(rows) + "\n").encode("utf-8"))
    report = training_preflight(df, [TITLE, DESC, KEYW], LABEL, label_separator=",", min_samples=1)

    assert report["dropped"]["duplicate"] == 1
    assert report["kept_after_cleaning"] == 2


def test_preflight_learnable_needs_rows_with_and_without_the_label_like_api_v3():
    """api_v3 (its audit of 2026-09-30, T01) trains a label only with ``min_samples`` rows WITH
    it and as many WITHOUT it -- one on every row teaches nothing -- and repeats that until
    nothing changes, since dropping the rows left without a label takes negatives away from the
    labels that stay. The expected values are api_v3's ``prepare_targets`` answers."""
    from app.refine.analyze import training_preflight
    from app.refine.store import read_csv

    def preflight(cells: list[str]) -> dict:
        good = "Ein ausreichend langer Beispieltext zum Thema"
        rows = [f"Titel {i};{good} {i};kw;{cell}" for i, cell in enumerate(cells)]
        df = read_csv((CSV_HEADER + "\n".join(rows) + "\n").encode("utf-8"))
        return training_preflight(df, [TITLE, DESC, KEYW], LABEL, label_separator=",", min_samples=5)

    on_every_row = preflight(["A,B"] * 10 + ["A,C"] * 20)
    assert on_every_row["per_label_effective"] == {"B": 10, "C": 20}
    assert on_every_row["effective_rows"] == 30
    assert on_every_row["ubiquitous_labels"] == {"A": 30}
    assert on_every_row["labels_below_min"] == {}

    cascade = preflight(["A,B"] * 10 + ["A"] * 20)
    assert cascade["learnable_labels"] == 0
    assert cascade["effective_rows"] == 0
    assert cascade["ubiquitous_labels"] == {"A": 30, "B": 10}


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


def test_preflight_route_defaults_min_samples_like_api_v3(make_client):
    """api_v3's /train applies 20 when `min_samples_per_label` is left out -- its own UI leaves a
    blank field out -- and scales it with the dataset only for an explicit null (since
    2026-09-08). The preflight still treated "left out" as the automatic value."""
    client = make_client()
    assert _import(client, _preflight_csv()).status_code == 200
    body = {"text_columns": [TITLE, DESC, KEYW], "label_column": LABEL}

    omitted = client.post("/refine/curated/preflight", json=body, headers=HEADERS)
    auto = client.post("/refine/curated/preflight", json={**body, "min_samples": None},
                       headers=HEADERS)

    assert omitted.status_code == 200, omitted.text
    assert omitted.json()["min_samples"] == 20
    assert auto.json()["min_samples"] == 2  # api_v3's automatic value under 1,000 rows


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

    # min_samples 1: of the three kept rows two carry A, and at 2 api_v3 would also need two
    # rows WITHOUT it (T01) -- this smoke test is about the route, not that rule.
    r = client.post("/refine/curated/preflight",
                    json={"text_columns": [TITLE, DESC, KEYW], "label_column": LABEL,
                          "min_samples": 1},
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
