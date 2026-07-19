"""SKOS vocabulary parsing (SkoHub JSON-LD): concept index, hierarchy, subtree.

Shape observed on vocabs.openeduhub.de (see tests/fixtures): a ConceptScheme
with ``hasTopConcept``; concepts carry ``id``, language-mapped ``prefLabel`` /
``altLabel`` and optionally embedded ``narrower`` children. Real files may be
completely flat (both fixture vocabularies are) or nested — both must work.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class Concept:
    """One SKOS concept with its place in the hierarchy."""

    uri: str
    pref_label: dict[str, str]
    alt_labels: dict[str, list[str]]
    broader: str | None
    narrower: tuple[str, ...]


@dataclass
class Vocabulary:
    """Parsed vocabulary: concept index in DFS tree order plus scheme metadata."""

    scheme_uri: str
    title: dict[str, str]
    concepts: dict[str, Concept] = field(default_factory=dict)
    top: list[str] = field(default_factory=list)

    def label(self, uri: str, langs: tuple[str, ...] = ("de", "en")) -> str:
        """Preferred label following the language preference, falling back to
        any available language and finally to the URI itself."""
        concept = self._get(uri)
        for lang in langs:
            if lang in concept.pref_label:
                return concept.pref_label[lang]
        return next(iter(concept.pref_label.values()), uri)

    def subtree(self, root_uri: str) -> list[str]:
        """The root concept plus all its descendants, in tree (DFS) order."""
        root = self._get(root_uri)
        uris = [root_uri]
        for child in root.narrower:
            uris.extend(self.subtree(child))
        return uris

    def tree(self) -> list[tuple[str, int]]:
        """All concepts as ``(uri, depth)`` in DFS order — for tree displays."""
        out: list[tuple[str, int]] = []

        def walk(uri: str, depth: int) -> None:
            out.append((uri, depth))
            for child in self.concepts[uri].narrower:
                walk(child, depth + 1)

        for top_uri in self.top:
            walk(top_uri, 0)
        return out

    def anchor(self, uri: str, langs: tuple[str, ...] = ("de", "en")) -> str:
        """Scheme title → broader path → own label; anchors prompts in the
        hierarchy because raw URIs mean nothing to a language model."""
        concept = self._get(uri)
        path: list[str] = []
        cursor = concept.broader
        while cursor is not None:
            path.append(self.label(cursor, langs))
            cursor = self.concepts[cursor].broader
        scheme = next(
            (self.title[lang] for lang in langs if lang in self.title),
            next(iter(self.title.values()), ""),
        )
        return " → ".join(p for p in (scheme, *reversed(path), self.label(uri, langs)) if p)

    def languages(self) -> set[str]:
        """Every language that appears in at least one preferred label."""
        langs: set[str] = set()
        for concept in self.concepts.values():
            langs.update(concept.pref_label)
        return langs

    def _get(self, uri: str) -> Concept:
        concept = self.concepts.get(uri)
        if concept is None:
            raise ValueError(f"Unknown concept: {uri}")
        return concept


def parse_vocabulary(raw: dict) -> Vocabulary:
    """Build a :class:`Vocabulary` from SkoHub JSON-LD; raises ``ValueError``
    (with a client-safe message) on anything that is not a SKOS ConceptScheme."""
    top = raw.get("hasTopConcept") if isinstance(raw, dict) else None
    if not isinstance(top, list) or not top:
        raise ValueError("Not a SKOS ConceptScheme: expected a non-empty 'hasTopConcept' list.")
    vocab = Vocabulary(scheme_uri=str(raw.get("id", "")), title=_lang_map(raw.get("title")))
    for node in top:
        vocab.top.append(_parse_concept(node, None, vocab))
    return vocab


def _parse_concept(node: object, broader: str | None, vocab: Vocabulary) -> str:
    if not isinstance(node, dict) or not node.get("id"):
        raise ValueError("Not a SKOS concept: every entry needs an 'id'.")
    children = node.get("narrower") or []
    if not isinstance(children, list) or any(
        not isinstance(c, dict) or not c.get("id") for c in children
    ):
        raise ValueError("Not a SKOS concept: 'narrower' must be a list of concepts with an 'id'.")
    uri = str(node["id"])
    # Register the parent before recursing so dict insertion order stays DFS.
    vocab.concepts[uri] = Concept(
        uri=uri,
        pref_label=_lang_map(node.get("prefLabel")),
        alt_labels=_alt_map(node.get("altLabel")),
        broader=broader,
        narrower=tuple(str(c["id"]) for c in children),
    )
    for child in children:
        _parse_concept(child, uri, vocab)
    return uri


def _lang_map(value: object) -> dict[str, str]:
    if not isinstance(value, dict):
        return {}
    return {str(k): v for k, v in value.items() if isinstance(v, str)}


def _alt_map(value: object) -> dict[str, list[str]]:
    """altLabel is a language map of lists; tolerate the single-string form."""
    if not isinstance(value, dict):
        return {}
    out: dict[str, list[str]] = {}
    for lang, labels in value.items():
        if isinstance(labels, list):
            out[str(lang)] = [str(x) for x in labels]
        elif isinstance(labels, str):
            out[str(lang)] = [labels]
    return out
