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
