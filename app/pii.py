"""PII engine — the ONE place every PII decision lives: pipeline input scrub
(references), output gate (validation), and refine scan/scrub all call this.

v1 is regex-based and deliberately conservative: educational texts are full of
numbers (years, dates, ISBNs, resolutions) that must never count as phone
numbers, so the phone pattern requires a +country or 0-prefixed block. URLs are
treated as PII-adjacent by design — in publishable synthetic data any URL is an
attribution/leak risk.

simplify: person NAMES are not detected in v1 — regexes cannot do that
reliably. Upgrade path (documented in the design doc): a spaCy-NER pass behind
this same scan/scrub API, plus the LLM sample gate arriving with M4.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

MASKS = {"email": "[email]", "url": "[url]", "phone": "[phone]", "handle": "[handle]"}

_EMAIL = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
_URL = re.compile(r"(?:https?://|www\.)\S+")
# Requires + or a 0-prefixed block and 6+ trailing digits; '.' is not a valid
# separator, which keeps dates (01.02.2026) and ISBNs out.
_PHONE = re.compile(
    r"(?<![\w.])(?:\+\d{1,3}(?:[ /-]?\d){6,}|(?:\(0\d{1,4}\)|0\d{1,4})(?:[ /-]?\d){5,})(?!\w)"
)
_HANDLE = re.compile(r"(?<![\w@.])@[A-Za-z0-9_][A-Za-z0-9_.]{1,29}")

# Order matters: emails before handles (both contain '@'), URLs before phones
# (URL paths contain digit runs). Mask tokens are inert for every pattern,
# which is what makes scrub idempotent.
_PIPELINE = (("email", _EMAIL), ("url", _URL), ("phone", _PHONE), ("handle", _HANDLE))
_TRAILING_PUNCTUATION = ".,;:!?)"


def scrub(text: str) -> tuple[str, dict[str, int]]:
    """Mask every PII occurrence; returns the cleaned text and per-category counts.

    The counts dict only contains categories that actually occurred — an empty
    dict means the text was clean.
    """
    found: dict[str, int] = {}
    for category, pattern in _PIPELINE:

        def _mask(match: re.Match[str], _category: str = category) -> str:
            value = match.group(0)
            if _category == "url":
                # Keep sentence punctuation that the greedy match swallowed.
                stripped = value.rstrip(_TRAILING_PUNCTUATION)
                return MASKS[_category] + value[len(stripped):]
            return MASKS[_category]

        text, count = pattern.subn(_mask, text)
        if count:
            found[category] = count
    return text, found


def scan(text: str) -> dict[str, int]:
    """Per-category occurrence counts without modifying anything (no plaintext)."""
    return scrub(text)[1]


def apply(text: str, action: str = "mask") -> tuple[str | None, dict[str, int]]:
    """Apply a PII policy to one text.

    ``mask`` returns the masked text; ``drop`` returns ``None`` when any PII was
    found (caller removes the row) and the untouched text otherwise.
    """
    if action not in ("mask", "drop"):
        raise ValueError(f"Unknown PII action {action!r}: use 'mask' or 'drop'.")
    cleaned, found = scrub(text)
    if action == "drop":
        return (None, found) if found else (text, found)
    return cleaned, found


@dataclass
class PiiReport:
    """Aggregate scan/scrub statistics — never stores matched text."""

    rows_scanned: int = 0
    rows_affected: int = 0
    counts: dict[str, int] = field(default_factory=dict)

    def record(self, found: dict[str, int]) -> None:
        self.rows_scanned += 1
        if found:
            self.rows_affected += 1
            for category, count in found.items():
                self.counts[category] = self.counts.get(category, 0) + count

    def as_dict(self) -> dict:
        return {
            "rows_scanned": self.rows_scanned,
            "rows_affected": self.rows_affected,
            "counts": dict(self.counts),
        }
