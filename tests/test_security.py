"""Input-safety unit tests for security.safe_name (path traversal rejection)."""

from __future__ import annotations

import pytest
from fastapi import HTTPException

from app.security import safe_name


def test_safe_name_accepts_plain_names():
    assert safe_name("my-dataset_v2") == "my-dataset_v2"
    assert safe_name("Faecher 2026") == "Faecher 2026"


@pytest.mark.parametrize(
    "bad",
    ["", "..", "a/../b", "a/b", "a\\b", ".hidden", "nul\x00byte"],
)
def test_safe_name_rejects_path_characters(bad):
    with pytest.raises(HTTPException) as exc:
        safe_name(bad)
    assert exc.value.status_code == 400


def test_safe_name_bounds_bytes_not_characters():
    """The filesystem limit is 255 BYTES per name (Linux NAME_MAX), and every
    store appends a suffix (".meta.json", a temp marker). A character count
    got both directions wrong: it refused the 108-character names split
    derives from a valid 100-character target, and let 70 emoji -- 280 bytes,
    ENAMETOOLONG on the Linux image -- straight through."""
    assert safe_name("a" * 108) == "a" * 108  # a split target of 100 plus "_holdout"
    assert safe_name("a" * 200) == "a" * 200
    for too_long in ("a" * 201, "\U0001F4DA" * 70):
        with pytest.raises(HTTPException) as exc:
            safe_name(too_long)
        assert exc.value.status_code == 400
