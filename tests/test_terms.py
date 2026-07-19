"""Per-concept term banks: dependency-free candidate mining, LLM clean/extend,
and their injection into the generation prompt."""

from __future__ import annotations

import asyncio

import pandas as pd

from app.vocab import parse_vocabulary
from tests.test_vocab import NESTED

URI_PHYSICS = "https://example.org/vocab/physics"
URI_BIOLOGY = "https://example.org/vocab/biology"


def _reference() -> tuple[pd.DataFrame, dict]:
    df = pd.DataFrame(
        [
            {"t": "Optik und Brechung", "d": "Versuche zur Lichtbrechung an Linsen",
             "k": "Optik, Licht", "label": URI_PHYSICS},
            {"t": "Mechanik Grundlagen", "d": "Kraft und Hebel im Alltag verstehen",
             "k": "Kraft, Hebel", "label": URI_PHYSICS},
            {"t": "Zellaufbau", "d": "Aufbau der pflanzlichen Zelle",
             "k": "Zelle, Biologie", "label": URI_BIOLOGY},
        ]
    )
    meta = {"text_columns": ["t", "d", "k"], "label_column": "label"}
    return df, meta


# ----------------------------------------------------------------- extraction ----


def test_extract_candidates_mines_keywords_and_terms_per_concept():
    from app.terms import extract_candidates

    df, meta = _reference()
    vocab = parse_vocabulary(NESTED)
    cands = extract_candidates(df, meta, vocab)

    physics = [t.lower() for t in cands[URI_PHYSICS]]
    assert "optik" in physics and "kraft" in physics          # from the keyword column
    assert any(t in physics for t in ("brechung", "linsen", "lichtbrechung"))  # mined from text
    assert "zelle" in [t.lower() for t in cands[URI_BIOLOGY]]
    assert "und" not in physics and "der" not in physics      # stopwords filtered


def test_extract_candidates_drops_terms_common_across_concepts():
    from app.terms import extract_candidates

    distinctive = ["photosynthese", "bruchrechnung", "grammatik", "evolution",
                   "algebra", "syntax", "zellkern", "gleichung"]
    scheme = {
        "id": "urn:v/", "title": {"de": "V"},
        "hasTopConcept": [{"id": f"urn:v/c{i}", "prefLabel": {"de": f"Fach{i}"}} for i in range(8)],
    }
    vocab = parse_vocabulary(scheme)
    df = pd.DataFrame(
        [{"t": f"{word} material", "d": f"material zum thema {word}", "k": word, "label": f"urn:v/c{i}"}
         for i, word in enumerate(distinctive)]
    )
    meta = {"text_columns": ["t", "d", "k"], "label_column": "label"}
    cands = extract_candidates(df, meta, vocab)

    # "material" is in every concept -> dropped as non-distinctive; the subject
    # word survives.
    assert all("material" not in [t.lower() for t in terms] for terms in cands.values())
    assert "photosynthese" in [t.lower() for t in cands["urn:v/c0"]]


def test_extract_candidates_caps_rows_per_label():
    from app.terms import extract_candidates

    # Five rows for one concept, each a unique term; mining is capped at 2 rows.
    df = pd.DataFrame([{"t": f"term{i}", "d": "", "k": "", "label": URI_PHYSICS} for i in range(5)])
    meta = {"text_columns": ["t", "d", "k"], "label_column": "label"}
    vocab = parse_vocabulary(NESTED)
    terms = [t.lower() for t in extract_candidates(df, meta, vocab, max_rows_per_label=2)[URI_PHYSICS]]
    assert "term0" in terms and "term1" in terms
    assert "term4" not in terms          # rows beyond the cap are not mined


def test_extract_candidates_dedupes_case_insensitively_and_caps_output():
    from app.terms import extract_candidates

    df = pd.DataFrame([{"t": "", "d": "", "k": "Euro, euro, EURO, Cent, cent", "label": URI_PHYSICS}])
    meta = {"text_columns": ["t", "d", "k"], "label_column": "label"}
    vocab = parse_vocabulary(NESTED)
    full = [t.lower() for t in extract_candidates(df, meta, vocab)[URI_PHYSICS]]
    assert full.count("euro") == 1 and full.count("cent") == 1     # case variants collapse
    assert len(extract_candidates(df, meta, vocab, top_n=1)[URI_PHYSICS]) == 1  # output cap


def test_extract_candidates_respects_selectable_columns():
    from app.terms import extract_candidates

    df, meta = _reference()
    vocab = parse_vocabulary(NESTED)
    # Only mine the keyword column — the free text must not contribute.
    cands = extract_candidates(df, meta, vocab, keyword_columns=["k"], term_columns=[])
    physics = [t.lower() for t in cands[URI_PHYSICS]]
    assert "optik" in physics
    assert not any("brechung" in t for t in physics)


# -------------------------------------------------------------------- refine ----


class _FakeSession:
    def __init__(self, terms: list[str]) -> None:
        self._terms = terms

    async def complete(self, prompt, schema, max_output_tokens=0):  # noqa: ANN001
        return schema(terms=self._terms)


def test_refine_cleans_dedupes_and_scrubs():
    from app.terms import refine_concept_terms

    vocab = parse_vocabulary(NESTED)
    session = _FakeSession(["Optik", "optik", "  Brechung ", "kontakt test@x.org", "Linsen"])
    out = asyncio.run(refine_concept_terms(session, vocab, URI_PHYSICS, ["Optik"],
                                           context="Schule", n=10))
    lowered = [t.lower() for t in out]
    assert "optik" in lowered
    assert len(lowered) == len(set(lowered))         # case-insensitive dedupe
    assert not any("@" in t for t in out)            # PII scrubbed out of the terms


def test_refine_prompt_carries_candidates_context_and_anchor():
    from app.terms import refine_terms_prompt

    vocab = parse_vocabulary(NESTED)
    prompt = refine_terms_prompt(vocab, URI_PHYSICS, ["Brechung", "Linsen"],
                                 context="Deutsche Schule", n=25)
    assert "Brechung" in prompt and "Linsen" in prompt
    assert "Deutsche Schule" in prompt               # user context injected
    assert "Physik" in prompt                        # concept label / anchor


# ---------------------------------------------------------------- generation ----


def test_generation_prompt_injects_term_bank():
    from app.generation import build_generation_prompt

    vocab = parse_vocabulary(NESTED)
    common = dict(seeds=[], n=3, corridor=(300, 600), facet="Sekundarstufe I", avoid_titles=[])
    with_terms = build_generation_prompt(vocab, URI_PHYSICS, terms=["Brechung", "Linsen"], **common)
    without = build_generation_prompt(vocab, URI_PHYSICS, **common)
    assert "Brechung" in with_terms and "Linsen" in with_terms
    assert "variiere" in with_terms          # the variation instruction marker
    assert "variiere" not in without
