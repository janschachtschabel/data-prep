"""SkoHub-style Turtle (.ttl) → JSON-LD.

A dependency-free reader for the SKOS concept-scheme subset that SkoHub — and
SKOS tools in general — emit: prefixed names / IRIs, language-tagged literals,
and prefLabel / altLabel / broader / narrower / hasTopConcept / topConceptOf /
title. Blank nodes, RDF collections, and triple-quoted or typed literals are out
of scope; rdflib is the upgrade path for arbitrary Turtle.

The output is the JSON-LD dict shape :func:`app.vocab.parse_vocabulary` already
understands, so there is exactly ONE parse/validation path. The manual
concept-list input lives in :mod:`app.vocab_formats`.
"""

from __future__ import annotations

import re
from collections.abc import Generator, Iterator

# The IRIs we act on, compared AFTER prefix resolution so CURIEs and full IRIs
# are treated identically.
_SKOS = "http://www.w3.org/2004/02/skos/core#"
_RDF_TYPE = "http://www.w3.org/1999/02/22-rdf-syntax-ns#type"
_TITLE_IRIS = {"http://purl.org/dc/terms/title", "http://purl.org/dc/elements/1.1/title"}

_STRING_ESCAPES = {"n": "\n", "t": "\t", "r": "\r", '"': '"', "'": "'", "\\": "\\"}

# One token: kind + payload. Objects/subjects are ('iri', str); literals carry
# their language ('lit', value, lang); punctuation is (';' | ',' | '.').
_Token = tuple


# --------------------------------------------------------------- tokenizer ----


def _tokens(text: str) -> Iterator[_Token]:  # noqa: C901 - a lexer is inherently branchy
    """Yield Turtle tokens. Directives are consumed whole; comments are only
    stripped when a ``#`` starts a token (never inside ``<...>`` or a string),
    which is why SKOS predicate IRIs like ``skos:core#prefLabel`` survive."""
    i, n = 0, len(text)
    while i < n:
        c = text[i]
        if c in " \t\r\n":
            i += 1
        elif c == "#":
            nl = text.find("\n", i)
            i = n if nl == -1 else nl + 1
        elif c == "@":
            i = yield from _directive(text, i, n)
        elif c == "<":
            end = text.find(">", i + 1)
            if end == -1:
                raise ValueError("Turtle: unterminated IRI reference.")
            yield ("iri", text[i + 1 : end])
            i = end + 1
        elif c == '"':
            token, i = _string(text, i, n)
            yield token
        elif c in ";,.":
            yield ("punc", c)
            i += 1
        else:
            token, i = _bareword(text, i, n)
            yield token
            if text[i - 1 : i] == ".":  # a '.' glued to the end of the word ends the statement
                yield ("punc", ".")


def _directive(text: str, i: int, n: int) -> Generator[_Token, None, int]:
    """Consume ``@prefix pfx: <iri> .`` or ``@base <iri> .`` whole, returning the
    position just past it."""
    m = re.match(r"@(prefix|base)\s+", text[i:])
    if not m:
        raise ValueError("Turtle: unknown @directive.")
    kind = m.group(1)
    i += m.end()
    pfx = ""
    if kind == "prefix":
        pm = re.match(r"([^\s:]*):\s*", text[i:])
        if not pm:
            raise ValueError("Turtle: malformed @prefix declaration.")
        pfx = pm.group(1)
        i += pm.end()
    if i >= n or text[i] != "<":
        raise ValueError(f"Turtle: @{kind} must be followed by an IRI.")
    end = text.find(">", i + 1)
    if end == -1:
        raise ValueError("Turtle: unterminated IRI reference.")
    iri = text[i + 1 : end]
    i = end + 1
    while i < n and text[i] in " \t\r\n":
        i += 1
    if i < n and text[i] == ".":
        i += 1
    yield ("prefix", pfx, iri) if kind == "prefix" else ("base", iri)
    return i


def _string(text: str, i: int, n: int) -> tuple[_Token, int]:
    """Read a ``"..."`` literal with an optional ``@lang`` or ``^^datatype``."""
    j = i + 1
    buf: list[str] = []
    while j < n and text[j] != '"':
        if text[j] == "\\" and j + 1 < n:
            buf.append(_STRING_ESCAPES.get(text[j + 1], text[j + 1]))
            j += 2
        else:
            buf.append(text[j])
            j += 1
    if j >= n:
        raise ValueError("Turtle: unterminated string literal.")
    value = "".join(buf)
    j += 1
    lang = ""
    if j < n and text[j] == "@":
        k = j + 1
        while k < n and (text[k].isalnum() or text[k] == "-"):
            k += 1
        lang = text[j + 1 : k].lower()
        j = k
    elif text[j : j + 2] == "^^":  # typed literal: skip the datatype token
        j += 2
        if j < n and text[j] == "<":
            j = text.find(">", j) + 1
        else:
            while j < n and text[j] not in " \t\r\n;,.":
                j += 1
    return ("lit", value, lang), j


def _bareword(text: str, i: int, n: int) -> tuple[_Token, int]:
    """Read ``a`` (rdf:type) or a prefixed name. A trailing ``.`` (statement
    terminator glued to the word) is left for the caller to emit as punctuation."""
    m = re.match(r"[^\s;,]+", text[i:])
    assert m is not None  # the dispatcher only calls this on a non-delimiter char
    word = m.group(0)
    advance = len(word)  # keep the full span; a glued trailing '.' is re-emitted by the caller
    if word.endswith("."):
        word = word[:-1]
    return (("a",) if word == "a" else ("pname", word)), i + advance


# ------------------------------------------------------------------ parser ----


def _resolve(token: _Token, prefixes: dict[str, str], base: str | None) -> str | None:
    """Turn an ``iri``/``pname`` token into an absolute IRI (or ``None`` for an
    unknown prefix, which simply means 'a predicate/class we do not handle')."""
    if token[0] == "iri":
        iri = token[1]
        if base and "://" not in iri and not iri.startswith("urn:"):
            return base + iri
        return iri
    if token[0] == "pname":
        prefix, _, local = token[1].partition(":")
        namespace = prefixes.get(prefix)
        return namespace + local if namespace is not None else None
    return None


def _statements(
    tokens: list[_Token], prefixes: dict[str, str], base: str | None
) -> Iterator[tuple[str | None, str | None, _Token]]:
    """Walk subject / predicate-object lists, yielding resolved triples where the
    object token is either ``('iri', str)`` or ``('lit', value, lang)``."""
    idx, size = 0, len(tokens)
    while idx < size:
        subject = _resolve(tokens[idx], prefixes, base)
        idx += 1
        while idx < size and tokens[idx] != ("punc", "."):
            predicate = _RDF_TYPE if tokens[idx][0] == "a" else _resolve(tokens[idx], prefixes, base)
            idx += 1
            while idx < size:  # object list, separated by ','
                obj = tokens[idx]
                idx += 1
                yield subject, predicate, obj if obj[0] == "lit" else ("iri", _resolve(obj, prefixes, base))
                if tokens[idx : idx + 1] != [("punc", ",")]:
                    break
                idx += 1
            while tokens[idx : idx + 1] == [("punc", ";")]:  # predicate separators (tolerate trailing)
                idx += 1
        if idx < size and tokens[idx] == ("punc", "."):
            idx += 1


def turtle_to_jsonld(text: str) -> dict:
    """Convert SkoHub-style Turtle into the JSON-LD dict ``parse_vocabulary``
    consumes. Raises ``ValueError`` on malformed input or an empty/cyclic scheme."""
    all_tokens = list(_tokens(text))
    prefixes = {t[1]: t[2] for t in all_tokens if t[0] == "prefix"}
    base = next((t[1] for t in all_tokens if t[0] == "base"), None)
    body = [t for t in all_tokens if t[0] not in ("prefix", "base")]

    concepts: dict[str, dict] = {}
    schemes: set[str] = set()
    titles: dict[str, dict[str, str]] = {}
    top: list[str] = []

    def rec(uri: str) -> dict:
        return concepts.setdefault(
            uri, {"pref": {}, "alt": {}, "broader": None, "narrower": []}
        )

    for subject, predicate, obj in _statements(body, prefixes, base):
        if subject is None or predicate is None:
            continue
        is_iri = obj[0] == "iri" and obj[1]
        if predicate == _RDF_TYPE and is_iri:
            if obj[1] == _SKOS + "ConceptScheme":
                schemes.add(subject)
            elif obj[1] == _SKOS + "Concept":
                rec(subject)
        elif predicate in _TITLE_IRIS and obj[0] == "lit":
            titles.setdefault(subject, {})[obj[2] or "de"] = obj[1]
        elif predicate == _SKOS + "prefLabel" and obj[0] == "lit":
            rec(subject)["pref"][obj[2] or "de"] = obj[1]
        elif predicate == _SKOS + "altLabel" and obj[0] == "lit":
            rec(subject)["alt"].setdefault(obj[2] or "de", []).append(obj[1])
        elif predicate == _SKOS + "broader" and is_iri:
            rec(subject)["broader"] = obj[1]
            rec(obj[1])
        elif predicate == _SKOS + "narrower" and is_iri:
            rec(subject)["narrower"].append(obj[1])
            rec(obj[1])
        elif predicate == _SKOS + "topConceptOf" and is_iri:
            schemes.add(obj[1])
        elif predicate == _SKOS + "hasTopConcept" and is_iri:
            schemes.add(subject)
            top.append(obj[1])
            rec(obj[1])

    for scheme in schemes:
        concepts.pop(scheme, None)  # a scheme is never one of its own concepts
    return _assemble(concepts, schemes, titles, top)


def _assemble(
    concepts: dict[str, dict], schemes: set[str], titles: dict[str, dict], top: list[str]
) -> dict:
    scheme_uri = next(iter(schemes), "")
    title = titles.get(scheme_uri) or next(iter(titles.values()), {})

    # Parent map: broader (child -> parent) is authoritative; narrower fills gaps.
    parent: dict[str, str] = {}
    for uri, rec in concepts.items():
        if rec["broader"] in concepts:
            parent[uri] = rec["broader"]
    for uri, rec in concepts.items():
        for child in rec["narrower"]:
            if child in concepts:
                parent.setdefault(child, uri)

    order = {uri: n for n, uri in enumerate(concepts)}
    children: dict[str, list[str]] = {uri: [] for uri in concepts}
    for child, par in parent.items():
        children[par].append(child)
    for kids in children.values():
        kids.sort(key=lambda uri: order[uri])

    roots: list[str] = []
    for uri in [*top, *(u for u in concepts if u not in parent)]:
        if uri in concepts and uri not in roots:
            roots.append(uri)

    def node(uri: str, seen: frozenset[str]) -> dict | None:
        if uri in seen:
            return None  # defensive: skos:broader/narrower could describe a cycle
        rec = concepts[uri]
        built: dict = {"id": uri, "prefLabel": rec["pref"]}
        if rec["alt"]:
            built["altLabel"] = rec["alt"]
        kids = [node(c, seen | {uri}) for c in children[uri]]
        kids = [k for k in kids if k]
        if kids:
            built["narrower"] = kids
        return built

    has_top = [node(uri, frozenset()) for uri in roots]
    has_top = [n for n in has_top if n]
    if not has_top:
        raise ValueError("Turtle: no SKOS concepts found (empty or cyclic scheme).")
    return {"id": scheme_uri, "title": title, "hasTopConcept": has_top}
