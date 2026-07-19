"""Text normalization — a faithful copy of api_v3's ``clean_text`` / ``split_labels``.

The training preflight MUST match api_v3's effective-row count to ±0, which is
only possible if the cleaning rules are byte-for-byte identical. This module is
therefore a deliberate, pinned mirror of api_v3/app/data.py — keep it in sync if
that file ever changes (a parity test guards the behaviour).
"""

from __future__ import annotations

import html
import math
import re

_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")
_HTML_TAG_RE = re.compile(r"<[^>]+>")
_MD_LINK_RE = re.compile(r"!?\[([^\]]*)\]\([^)]*\)")
_MD_MARK_RE = re.compile(r"[*_`~#>]+")
_WS_RE = re.compile(r"\s+")


def clean_text(value: object) -> str:
    """Normalize text for vectorization (HTML unescape, strip tags/markdown,
    drop control chars, collapse whitespace) — identical to api_v3.

    Callers combine columns after ``fillna("")`` like api_v3, so ``value`` is a
    string in practice; the None/NaN guards mirror api_v3 for direct use.
    """
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return ""
    text = html.unescape(str(value))
    text = _HTML_TAG_RE.sub(" ", text)
    text = _MD_LINK_RE.sub(r"\1", text)
    text = _MD_MARK_RE.sub("", text)
    text = _CONTROL_RE.sub("", text)
    return _WS_RE.sub(" ", text).strip()


def split_labels(value: object, separator: str = ",") -> list[str]:
    """Split a multilabel cell into trimmed, non-empty labels (api_v3 semantics)."""
    if value is None or value == "" or (isinstance(value, float) and math.isnan(value)):
        return []
    return [part.strip() for part in str(value).split(separator) if part.strip()]
