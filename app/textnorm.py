"""Text normalization — a faithful copy of api_v3's ``clean_text`` / ``split_labels``.

The training preflight MUST match api_v3's effective-row count to ±0, which is
only possible if the cleaning rules are byte-for-byte identical. This module is
therefore a deliberate, pinned mirror of api_v3's ``app/data.py`` and the
``app/markup.py`` patterns it composes — keep it in sync whenever they change
(parity tests guard the behaviour). It drifted twice without anyone noticing,
when api_v3 tightened its patterns (2026-09-20) and when it introduced cleaning
version 2 (audit 2026-09-30, T09).

api_v3 versions its cleaning: a bundle is served with the version it was trained
with. Only the CURRENT version is mirrored here (``data.CLEANING_VERSION`` = 2),
because everything data-prep prepares is meant for a new training.
"""

from __future__ import annotations

import hashlib
import html
import math
import re
import unicodedata

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
# A tag starts with `<` and a letter, `/`, `!` or `?`, as HTML requires, so `x < y ... a > b`
# stays prose (api_v3's STRICT_TAG_RE). Like every pattern here it excludes its own opening
# delimiter: `<[^>]+>` swallowed everything from a stray `<` to the next tag, and rescanned
# to the end of the text from each one.
_HTML_TAG_RE = re.compile(r"<[A-Za-z/!?][^<>]*>")
_MD_LINK_RE = re.compile(r"!?\[([^\[\]]*)\]\([^()]*\)")
_MD_MARK_RE = re.compile(r"[*_`~#>]+")
_WS_RE = re.compile(r"\s+")
# The Combining Diacritical Marks block: every Latin accent decomposes (NFKD) into its letter
# plus one of these, which is what api_v3's vectorizer (strip_accents="unicode") removes.
_COMBINING_MARKS_RE = re.compile("[̀-ͯ]+")


def clean_text(value: object) -> str:
    """Normalize text for vectorization (HTML unescape, strip tags/markdown,
    drop control chars, collapse whitespace) — identical to api_v3's cleaning version 2.

    Callers combine columns after ``fillna("")`` like api_v3, so ``value`` is a
    string in practice; the None/NaN guards mirror api_v3 for direct use. api_v3
    replaces a block-level tag with a line break rather than a space; the
    whitespace collapse at the end makes the two indistinguishable.
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    text = _HTML_TAG_RE.sub(" ", html.unescape(str(value)))
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _MD_MARK_RE.sub("", text)
    text = _CONTROL_RE.sub("", text)
    return _WS_RE.sub(" ", text).strip()


def dedupe_key(text: str) -> bytes:
    """The identity of a cleaned text as api_v3's dedupe sees it: two texts are duplicates when
    its vectorizer cannot tell them apart, because it lower-cases and strips accents
    (``dataset_load.dedupe_key``, audit 2026-09-30, T07). A 16-byte digest, as there: the
    caller keeps one key per kept row, not a second copy of every text. Marks outside the Latin
    block (other scripts' accents) stay, so such variants are not duplicates."""
    lowered = text.lower()
    if not lowered.isascii():
        lowered = _COMBINING_MARKS_RE.sub("", unicodedata.normalize("NFKD", lowered))
    return hashlib.blake2b(lowered.encode("utf-8"), digest_size=16).digest()


def split_labels(value: object, separator: str = ",") -> list[str]:
    """Split a multilabel cell into trimmed, non-empty labels (api_v3 semantics), every value
    kept. api_v3's own ``split_labels`` also drops container values; data-prep rewrites label
    cells and pairs them with display names by position, so it has to see every value, and
    the training preflight applies ``is_container_label`` itself."""
    if value is None or value == "" or (isinstance(value, float) and math.isnan(value)):
        return []
    return [part.strip() for part in str(value).split(separator) if part.strip()]


def is_container_label(label: str) -> bool:
    """A value ending in ``/`` names a namespace -- "members of" a vocabulary -- rather than a
    concept, and api_v3 never trains it (its ``label_names.is_container_label``)."""
    return label.endswith("/")
