"""Refine part 6: additive LLM enrichment (fills gaps only, marks
enriched_fields, never overwrites) for keywords and descriptions."""

from __future__ import annotations

import asyncio
import json

import httpx
import pandas as pd

from app.config import Budgets, LlmEndpoint

HEADERS = {"X-API-Key": "test-key"}

TITLE = "properties.cclom:title"
DESC = "properties.cclom:general_description"
KEYW = "properties.cclom:general_keyword"
LABEL = "properties.ccm:taxonid"


KEYWORD_GUIDANCE = "Nenne 3-6 treffende deutsche Schlagwörter (kommagetrennt)."
DESCRIPTION_GUIDANCE = "Schreibe 2-3 Sätze, die das Material sachlich beschreiben."


async def _fake_keywords(prompt, schema):
    # Includes PII to prove the scrub runs on what the model returned.
    return schema(values=["Optik", "Licht", "kontakt@x.de"])


async def _fake_description(prompt, schema):
    return schema(values=["Eine sachliche Beschreibung des Materials zum Thema."])


def _wlo_fields():
    from app.refine.fields import TextField

    return [
        TextField(column=TITLE),
        TextField(column=DESC, guidance=DESCRIPTION_GUIDANCE),
        TextField(column=KEYW, separator=",", min_values=3, guidance=KEYWORD_GUIDANCE),
    ]


# ------------------------------------------------------------- pure logic ----


def test_enrich_keywords_fills_gaps_marks_and_scrubs():
    """Unchanged behaviour, new signature: a gap is filled, a complete cell is left
    alone, the model's output is scrubbed, and the row records which field was
    written — now by column, because that is what identifies a field."""
    from app.refine.enrich import enrich_dataset

    df = pd.DataFrame(
        [["Optik", "Licht und Brechung", ""],            # empty keywords -> enriched
         ["Mechanik", "Kraft", "Kraft, Hebel, Physik"]],  # already has >= min -> untouched
        columns=[TITLE, DESC, KEYW],
    )
    new, stats = asyncio.run(enrich_dataset(
        df, fields=_wlo_fields(), target_field=KEYW, complete=_fake_keywords,
    ))
    assert stats["enriched"] == 1
    assert new.iloc[0][KEYW] == "Optik, Licht, [email]"  # PII scrubbed
    assert new.iloc[0]["enriched_fields"] == KEYW
    assert new.iloc[1][KEYW] == "Kraft, Hebel, Physik"   # untouched
    assert new.iloc[1]["enriched_fields"] == ""


def test_enrich_description_only_fills_empty():
    from app.refine.enrich import enrich_dataset

    df = pd.DataFrame(
        [["Optik", "", "kw"], ["Mechanik", "Vorhandene Beschreibung", "kw"]],
        columns=[TITLE, DESC, KEYW],
    )
    new, stats = asyncio.run(enrich_dataset(
        df, fields=_wlo_fields(), target_field=DESC, complete=_fake_description,
    ))
    assert stats["enriched"] == 1
    assert new.iloc[0][DESC] == "Eine sachliche Beschreibung des Materials zum Thema."
    assert new.iloc[1][DESC] == "Vorhandene Beschreibung"  # not overwritten


def test_a_field_nobody_anticipated_enriches_without_new_code():
    """The point of the generalisation. A semicolon-separated author list is not a
    role this module knows; it is a field with a separator, and it enriches like any
    other — including the join, which uses the field's own separator."""
    from app.refine.enrich import enrich_dataset
    from app.refine.fields import TextField

    authors = TextField(column="authors", separator=";", min_values=2,
                        guidance="Nenne zwei plausible Urheber.")
    fields = [TextField(column=TITLE), authors]
    df = pd.DataFrame([["Optik", "Meier"]], columns=[TITLE, "authors"])

    async def two_authors(prompt, schema):
        assert "Optik" in prompt, "the other fields' values belong in the prompt"
        assert "Nenne zwei plausible Urheber." in prompt, "the field's guidance too"
        return schema(values=["Meier", "Schulz"])

    new, stats = asyncio.run(enrich_dataset(
        df, fields=fields, target_field="authors", complete=two_authors))

    assert stats["enriched"] == 1
    assert new.iloc[0]["authors"] == "Meier; Schulz"
    assert new.iloc[0]["enriched_fields"] == "authors"


def test_an_unknown_target_field_is_refused():
    """A target that is not among the fields is a caller mistake, and a silent no-op
    would look like "nothing needed enrichment"."""
    import pytest

    from app.refine.enrich import enrich_dataset
    from app.refine.fields import TextField

    df = pd.DataFrame([["Optik"]], columns=[TITLE])
    with pytest.raises(ValueError, match="not among the fields"):
        asyncio.run(enrich_dataset(df, fields=[TextField(column=TITLE)],
                                   target_field="nonsense", complete=_fake_keywords))


# ------------------------------------------------------------------ route ----


def _import(client, df: pd.DataFrame, name: str = "curated"):
    payload = df.to_csv(sep=";", index=False).encode("utf-8")
    return client.post("/refine/datasets/import",
                       files={"file": (f"{name}.csv", payload, "text/csv")},
                       data={"name": name}, headers=HEADERS)


def _mock_session(payload: dict, monkeypatch):
    from app.llm import LlmSession

    monkeypatch.setenv("TEST_LLM_KEY", "k")
    body = {
        "id": "c", "object": "chat.completion", "created": 1, "model": "m",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": json.dumps(payload)}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 9, "total_tokens": 14},
    }
    transport = httpx.MockTransport(lambda request: httpx.Response(200, json=body))
    return LlmSession(endpoint=LlmEndpoint(model="gpt-5.4-nano", api_key_env="TEST_LLM_KEY"),
                      budgets=Budgets(), transport=transport)


def test_enrich_route_writes_target_with_marks(make_client, tmp_path, monkeypatch):
    import app.routes.refine_prep as refine_route

    client = make_client()
    _import(client, pd.DataFrame([["Optik", "Licht und Brechung", ""]],
                                 columns=[TITLE, DESC, KEYW, LABEL][:3]))
    session = _mock_session({"values": ["Optik", "Licht", "Physik"]}, monkeypatch)
    monkeypatch.setattr(refine_route, "session_for", lambda purpose, settings, override=None: session)

    r = client.post("/refine/curated/enrich",
                    json={"mode": "keywords", "title_column": TITLE, "description_column": DESC,
                          "keyword_column": KEYW, "min_keywords": 3, "target": "enriched"},
                    headers=HEADERS)
    assert r.status_code == 200, r.text
    assert r.json()["enriched"] == 1

    names = {d["name"] for d in client.get("/refine/datasets", headers=HEADERS).json()["datasets"]}
    assert "enriched" in names
    ops = json.loads((tmp_path / "data" / "refine" / "enriched.ops.json").read_text("utf-8"))
    assert ops[-1]["op"] == "enrich" and ops[-1]["field"] == KEYW


def test_enrich_route_rejects_unknown_mode(make_client, tmp_path, monkeypatch):
    import app.routes.refine_prep as refine_route

    client = make_client()
    _import(client, pd.DataFrame([["Optik", "Licht", "kw"]], columns=[TITLE, DESC, KEYW]))
    monkeypatch.setattr(refine_route, "session_for",
                        lambda purpose, settings, override=None: _mock_session({"values": ["x"]}, monkeypatch))
    r = client.post("/refine/curated/enrich",
                    json={"mode": "nonsense", "title_column": TITLE, "description_column": DESC,
                          "keyword_column": KEYW, "target": "out"},
                    headers=HEADERS)
    assert r.status_code == 422  # Literal schema rejects it


def test_enrich_route_maps_llm_config_error_to_503(make_client, monkeypatch):
    """A missing API key is LlmConfigError: the upstream was never reached, so
    502 is the wrong answer. The seed routes already map it to 503; enrich
    answered 502 for the same condition (audit 2026-09-11, API1)."""
    import app.routes.refine_prep as refine_route
    from app.llm import LlmConfigError

    client = make_client()
    _import(client, pd.DataFrame([["Optik", "Licht", ""]], columns=[TITLE, DESC, KEYW]))
    monkeypatch.setattr(refine_route, "session_for",
                        lambda purpose, settings, override=None: _mock_session({"values": ["x"]}, monkeypatch))

    async def _boom(*_args, **_kwargs):
        raise LlmConfigError("Environment variable 'TEST_LLM_KEY' is not set")

    monkeypatch.setattr(refine_route, "enrich_dataset", _boom)
    r = client.post("/refine/curated/enrich",
                    json={"mode": "keywords", "title_column": TITLE, "description_column": DESC,
                          "keyword_column": KEYW, "target": "out"},
                    headers=HEADERS)
    assert r.status_code == 503
    assert "not set" in r.json()["detail"]


def test_both_request_shapes_produce_the_same_dataset(make_client, monkeypatch):
    """The old shape was the only one until now, so it has to keep meaning exactly what
    it meant: the three WLO columns with the keyword prompt. Sending the equivalent
    field specification must land on the same rows, or the compatibility mapping is
    decoration rather than a promise."""
    import app.routes.refine_prep as refine_route

    client = make_client()
    rows = pd.DataFrame([["Optik", "Licht und Brechung", ""]], columns=[TITLE, DESC, KEYW])
    _import(client, rows, name="src")
    session = _mock_session({"values": ["Optik", "Licht", "Physik"]}, monkeypatch)
    monkeypatch.setattr(refine_route, "session_for", lambda purpose, settings, override=None: session)

    old = client.post("/refine/src/enrich", headers=HEADERS, json={
        "mode": "keywords", "title_column": TITLE, "description_column": DESC,
        "keyword_column": KEYW, "min_keywords": 3, "target": "by_mode"})
    new = client.post("/refine/src/enrich", headers=HEADERS, json={
        "target_field": KEYW, "target": "by_fields",
        "fields": [
            {"column": TITLE},
            {"column": DESC},
            {"column": KEYW, "separator": ",", "min_values": 3,
             "guidance": "Nenne 3-6 treffende deutsche Schlagwörter (kommagetrennt), "
                         "die den Inhalt erschließen."},
        ]})
    assert old.status_code == 200, old.text
    assert new.status_code == 200, new.text

    by_mode = client.get("/refine/by_mode/rows?limit=5", headers=HEADERS).json()
    by_fields = client.get("/refine/by_fields/rows?limit=5", headers=HEADERS).json()
    assert by_mode == by_fields


def test_a_request_with_neither_shape_is_refused(make_client, monkeypatch):
    """Without a mode and without fields there is nothing to fill, and guessing a
    default would enrich a column the caller never named."""
    import app.routes.refine_prep as refine_route

    client = make_client()
    _import(client, pd.DataFrame([["Optik", "Licht", ""]], columns=[TITLE, DESC, KEYW]))
    monkeypatch.setattr(refine_route, "session_for",
                        lambda purpose, settings, override=None: _mock_session({"values": ["x"]}, monkeypatch))

    r = client.post("/refine/curated/enrich", headers=HEADERS, json={"target": "out"})
    assert r.status_code == 400
    assert "fields" in r.json()["detail"]


# -------------------------------------------------------- review remediation ----


def test_a_short_list_keeps_its_curated_values_and_gains_new_ones():
    """Two keywords below a minimum of three is a SHORT list, not an empty one. The
    gap was filled by replacing it — 'Mathe, Algebra' became 'Optik' — although the
    promise was never to overwrite curated content (review #6, D3)."""
    from app.refine.enrich import enrich_dataset

    df = pd.DataFrame([["Zahlen", "Rechnen lernen", "Mathe, Algebra"]],
                      columns=[TITLE, DESC, KEYW])

    async def more(prompt, schema):
        return schema(values=["algebra", "Geometrie", "Zahlen"])

    new, stats = asyncio.run(enrich_dataset(
        df, fields=_wlo_fields(), target_field=KEYW, complete=more))

    assert new.iloc[0][KEYW] == "Mathe, Algebra, Geometrie, Zahlen"
    assert stats["enriched"] == 1


def test_the_model_is_shown_what_the_list_already_holds():
    """Without the existing values it cannot add to them — it can only guess again."""
    from app.refine.enrich import enrich_dataset

    df = pd.DataFrame([["Zahlen", "Rechnen lernen", "Mathe, Algebra"]],
                      columns=[TITLE, DESC, KEYW])
    prompts: list[str] = []

    async def capture(prompt, schema):
        prompts.append(prompt)
        return schema(values=["Geometrie"])

    asyncio.run(enrich_dataset(df, fields=_wlo_fields(), target_field=KEYW, complete=capture))

    assert "Mathe" in prompts[0] and "Algebra" in prompts[0]


def test_an_empty_answer_changes_nothing_and_is_not_counted():
    """An empty answer wiped the cell and still counted as enriched (review #6)."""
    from app.refine.enrich import enrich_dataset

    df = pd.DataFrame([["Zahlen", "Rechnen lernen", "Mathe"]], columns=[TITLE, DESC, KEYW])

    async def nothing(prompt, schema):
        return schema(values=[])

    new, stats = asyncio.run(enrich_dataset(
        df, fields=_wlo_fields(), target_field=KEYW, complete=nothing))

    assert new.iloc[0][KEYW] == "Mathe"
    assert new.iloc[0]["enriched_fields"] == ""
    assert stats["enriched"] == 0


def test_an_answer_that_adds_nothing_new_is_not_counted():
    from app.refine.enrich import enrich_dataset

    df = pd.DataFrame([["Zahlen", "Rechnen lernen", "Mathe"]], columns=[TITLE, DESC, KEYW])

    async def repeat(prompt, schema):
        return schema(values=["mathe"])

    new, stats = asyncio.run(enrich_dataset(
        df, fields=_wlo_fields(), target_field=KEYW, complete=repeat))

    assert new.iloc[0][KEYW] == "Mathe"
    assert stats["enriched"] == 0


def test_the_prompt_names_the_field_and_its_shape_even_without_guidance():
    """The UI sends no guidance unless someone types one, and the prompt never said
    which field to fill: the model got context lines and a PII rule, nothing else
    (review #7, D8)."""
    from app.refine.enrich import enrich_dataset
    from app.refine.fields import TextField

    fields = [TextField(column=TITLE),
              TextField(column=KEYW, separator=";", min_values=4)]
    df = pd.DataFrame([["Zahlen", ""]], columns=[TITLE, KEYW])
    prompts: list[str] = []

    async def capture(prompt, schema):
        prompts.append(prompt)
        return schema(values=["a", "b", "c", "d"])

    asyncio.run(enrich_dataset(df, fields=fields, target_field=KEYW, complete=capture))

    assert KEYW in prompts[0]
    assert "4" in prompts[0]
    assert '";"' in prompts[0] or "';'" in prompts[0]


def test_a_target_field_that_is_not_among_the_fields_is_a_bad_request(make_client):
    """The engine refuses it with a ValueError the route did not map — a 500 (#10)."""
    client = make_client()
    _import(client, pd.DataFrame([["Optik", "Licht", ""]], columns=[TITLE, DESC, KEYW]))

    r = client.post("/refine/curated/enrich", headers=HEADERS, json={
        "fields": [{"column": TITLE}, {"column": DESC}], "target_field": KEYW,
        "target": "out"})

    assert r.status_code == 400
    assert KEYW in r.json()["detail"]


def test_enrichment_refuses_fields_it_would_misuse(make_client):
    """`enriched_fields` as a text field would have its provenance overwritten by
    generated text; a column named twice would be filled twice (#25)."""
    client = make_client()
    _import(client, pd.DataFrame([["Optik", "Licht", ""]], columns=[TITLE, DESC, KEYW]))

    for fields in ([{"column": TITLE}, {"column": "enriched_fields"}],
                   [{"column": TITLE}, {"column": TITLE}]):
        r = client.post("/refine/curated/enrich", headers=HEADERS, json={
            "fields": fields, "target_field": TITLE, "target": "out"})
        assert r.status_code == 422, (fields, r.status_code, r.text)


def test_an_unconfigured_purpose_is_503_for_enrichment_too(make_client, monkeypatch):
    import app.routes.refine_prep as refine_route
    from app.llm import LlmConfigError

    client = make_client()
    _import(client, pd.DataFrame([["Optik", "Licht", ""]], columns=[TITLE, DESC, KEYW]))

    def unconfigured(*_args, **_kwargs):
        raise LlmConfigError("No LLM endpoint configured for purpose 'bulk'.")

    monkeypatch.setattr(refine_route, "session_for", unconfigured)

    r = client.post("/refine/curated/enrich", headers=HEADERS, json={
        "mode": "keywords", "title_column": TITLE, "description_column": DESC,
        "keyword_column": KEYW, "target": "out"})

    assert r.status_code == 503
