"""Per-concept term banks: mine candidate keywords/entities from reference rows,
then let the LLM clean and extend them for a concept + context. The bank feeds
generation so synthetic content covers the field's real vocabulary and varies.

Extraction is dependency-free — split keyword fields + stopword-filtered word
frequency on the chosen text columns. The semantic work (recognising the real
entities, removing noise, adding missing terms) is the LLM's job, so no NER model
(spaCy) is pulled in. Which columns feed keywords vs. free-text terms is
selectable; both default to the reference's configured text columns.
"""

from __future__ import annotations

import re
from collections import Counter

import pandas as pd
from pydantic import BaseModel, Field

from .llm import LlmSession
from .pii import scrub
from .textnorm import split_labels
from .vocab import Vocabulary

# A compact bilingual stop list — extraction only gathers candidates, the LLM
# does the real clean-up, so this need not be exhaustive.
_STOPWORD_TEXT = (
    "der die das und oder in im mit fuer für von zu den dem des ein eine einen einer "
    "eines auf aus bei nach ueber über unter vor durch als am an ist sind war waren "
    "werden wird worden kann koennen können soll sollte muss muessen müssen darf würde "
    "man sich auch nicht nur noch sehr mehr weniger viel viele wenige alle jede jeder "
    "jedes kein keine dann wann weil dass damit sowie bzw etc usw also schon immer "
    "wieder gibt haben hat hatte sein seine ihr ihre unser wir ich du er es sie uns "
    "euch hier da dort so wie wo warum wieso was wer welche welcher welches etwas "
    "nichts alles jemand zum zur beim vom dieser diese dieses jene jener solche "
    "the a an and or of to in for with without on at is are was were be been being "
    "this that these those from into about it its we you they i he she there here very "
    "more most some any no not can could will would should must may your our their "
    "http https www com de org url oer ca ggf inkl bzw"
)
_STOPWORDS = frozenset(_STOPWORD_TEXT.split())

_TOKEN = re.compile(r"[0-9A-Za-zÄÖÜäöüß][0-9A-Za-zÄÖÜäöüß-]{1,}")

# Rough per-batch injection size — a rotating subset keeps prompts small and the
# output varied across a concept's batches.
TERMS_PER_BATCH = 12


class TermList(BaseModel):
    """LLM structured output: a cleaned, extended domain-term list."""

    terms: list[str] = Field(default_factory=list)


def _split_keywords(value: str) -> list[str]:
    return [part.strip() for part in re.split(r"[;,|/]", value) if part.strip()]


def _content_terms(text: str) -> list[str]:
    return [
        token.lower()
        for token in _TOKEN.findall(text)
        if len(token) > 2 and token.lower() not in _STOPWORDS
    ]


def extract_candidates(
    df: pd.DataFrame,
    meta: dict,
    vocab: Vocabulary,
    *,
    keyword_columns: list[str] | None = None,
    term_columns: list[str] | None = None,
    top_n: int = 60,
    max_rows_per_label: int = 500,
    max_concept_fraction: float = 0.4,
) -> dict[str, list[str]]:
    """Per-concept candidate terms mined from the reference (no LLM): whole
    keywords from ``keyword_columns`` plus frequent content words from
    ``term_columns``. Up to ``max_rows_per_label`` rows are mined per concept
    (separate from the few-shot seed count). Terms that appear across too many
    concepts (common everywhere → not distinctive, e.g. "lernen"/"schule") are
    dropped, then case variants are collapsed; the LLM refine step handles the
    finer clean-up. Returns ``{uri: [term, ...]}`` capped at ``top_n``."""
    df = df.fillna("")
    text_cols = list(meta["text_columns"])
    kw_cols = keyword_columns if keyword_columns is not None else text_cols[2:3]
    tx_cols = term_columns if term_columns is not None else text_cols[:2]
    counters: dict[str, Counter] = {}
    rows_seen: dict[str, int] = {}
    for _, row in df.iterrows():
        candidates: list[str] = []
        for column in kw_cols:
            candidates.extend(_split_keywords(str(row.get(column, ""))))
        for column in tx_cols:
            candidates.extend(_content_terms(str(row.get(column, ""))))
        candidates = [c for c in (x.strip() for x in candidates) if c]
        if not candidates:
            continue
        for uri in split_labels(str(row.get(meta["label_column"], ""))):
            if uri in vocab.concepts and rows_seen.get(uri, 0) < max_rows_per_label:
                counters.setdefault(uri, Counter()).update(candidates)
                rows_seen[uri] = rows_seen.get(uri, 0) + 1

    common: set[str] = set()
    if len(counters) >= 6:  # only meaningful with enough concepts to compare
        document_frequency: Counter = Counter()
        for counter in counters.values():
            document_frequency.update(counter.keys())
        cutoff = max(5, int(max_concept_fraction * len(counters)))
        common = {term for term, freq in document_frequency.items() if freq > cutoff}
    return {
        uri: _dedupe_ci(term for term, _ in counter.most_common() if term not in common)[:top_n]
        for uri, counter in counters.items()
    }


def _dedupe_ci(terms) -> list[str]:
    """Case-insensitive dedupe, keeping the first (highest-frequency) casing."""
    seen: set[str] = set()
    out: list[str] = []
    for term in terms:
        key = term.lower()
        if key not in seen:
            seen.add(key)
            out.append(term)
    return out


_REFINE_PROMPT = """Kontext: Ein Katalog für BILDUNGSINHALTE. Fachgebiet/Thema: "{label}".
Einordnung in der Systematik: {anchor}.{context}

Roh gesammelte Begriffe/Schlagwörter aus echten Katalogeinträgen zu diesem Fach:
{candidates}

Aufgabe: Erstelle daraus eine BEREINIGTE, fachlich passende Begriffs- und Entitätenliste:
- Entferne Rauschen, Fachfremdes, zu Allgemeines und Dubletten; normalisiere die Schreibweise.
- ERGÄNZE wichtige, fachtypische Begriffe/Entitäten, die zum Fach und Kontext passen und fehlen.
- Ziel: {n} prägnante Begriffe (Substantive, Fachbegriffe, Entitäten) — keine ganzen Sätze.
- KEINE Personennamen, E-Mail-Adressen, Telefonnummern, URLs.
Antworte NUR mit JSON: {{"terms": ["...", "..."]}}"""


def refine_terms_prompt(
    vocab: Vocabulary, uri: str, candidates: list[str], *, context: str = "", n: int = 30
) -> str:
    """Build the clean-and-extend prompt (anchored in the hierarchy, grounded in
    the mined candidates, steered by the optional user context)."""
    clause = f"\nVorgabe des Nutzers (bitte beachten): {context.strip()}" if context.strip() else ""
    candidate_str = ", ".join(candidates[:60]) or "(keine — leite aus Fach und Kontext ab)"
    return _REFINE_PROMPT.format(
        label=vocab.label(uri), anchor=vocab.anchor(uri), context=clause, candidates=candidate_str, n=n
    )


async def refine_concept_terms(
    session: LlmSession,
    vocab: Vocabulary,
    uri: str,
    candidates: list[str],
    *,
    context: str = "",
    n: int = 30,
) -> list[str]:
    """LLM-clean and extend the candidate list for one concept; results are
    case-insensitively deduped, PII-scrubbed (defense in depth) and capped."""
    batch = await session.complete(
        refine_terms_prompt(vocab, uri, candidates, context=context, n=n),
        TermList,
        max_output_tokens=1500,
    )
    seen: set[str] = set()
    cleaned: list[str] = []
    for term in batch.terms:
        value = scrub(term)[0].strip()
        key = value.lower()
        if value and key not in seen:
            seen.add(key)
            cleaned.append(value)
        if len(cleaned) >= n:
            break
    return cleaned
