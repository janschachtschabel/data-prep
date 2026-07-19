"""Manual concept lists → JSON-LD.

Turns a pasted list (one concept per line, ``Label`` or ``Label | URI`` in
either order) into the JSON-LD dict shape :func:`app.vocab.parse_vocabulary`
already understands, so there is exactly ONE parse/validation path and the store
keeps persisting plain JSON. SkoHub Turtle (.ttl) input lives in
:mod:`app.vocab_turtle`.
"""

from __future__ import annotations

import re
import unicodedata


def _slug(label: str) -> str:
    ascii_form = unicodedata.normalize("NFKD", label).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", "-", ascii_form.lower()).strip("-")


def _looks_like_uri(text: str) -> bool:
    return "://" in text or text.startswith("urn:")


def _split_manual_line(line: str) -> tuple[str | None, str]:
    """Return ``(uri_or_None, label)`` for one manual line. A ``|`` separates a
    URI from its label in EITHER order; a bare token is a label (URI minted) or,
    if it already looks like a URI, a URI whose label is its last path segment."""
    if "|" in line:
        left, _, right = line.partition("|")
        left, right = left.strip(), right.strip()
        if _looks_like_uri(left) and not _looks_like_uri(right):
            return left, right
        if _looks_like_uri(right):
            return right, left
        return None, left
    if _looks_like_uri(line):
        segment = re.split(r"[/#:]", line.rstrip("/#"))[-1]
        return line, segment or line
    return None, line


def manual_to_jsonld(text: str, *, title: str, lang: str, base_uri: str) -> dict:
    """Build a flat JSON-LD concept scheme from a pasted list — one concept per
    line (``Label`` or ``Label | URI``). Raises ``ValueError`` if nothing usable
    is found."""
    lang = lang or "de"
    base = base_uri.rstrip("/")
    nodes: list[dict] = []
    used: set[str] = set()
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#"):
            continue
        uri, label = _split_manual_line(line)
        if not label:
            continue
        if uri is None:
            uri = f"{base}/{_slug(label) or f'c{len(nodes) + 1}'}"
        unique, suffix = uri, 2
        while unique in used:
            unique, suffix = f"{uri}-{suffix}", suffix + 1
        used.add(unique)
        nodes.append({"id": unique, "prefLabel": {lang: label}})
    if not nodes:
        raise ValueError("No concepts found — add one label per line.")
    return {"id": base_uri, "title": {lang: title or base_uri}, "hasTopConcept": nodes}
