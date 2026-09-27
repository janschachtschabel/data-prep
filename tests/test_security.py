"""Input safety and module boundary of `app.security` (path traversal, device names, auth)."""

from __future__ import annotations

import subprocess
import sys

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


@pytest.mark.parametrize(
    "device",
    ["con", "NUL", "aux", "prn", "com1", "lpt9", "con.csv", "nul ", "nul.", "CONOUT$"],
)
def test_safe_name_rejects_windows_device_names(device):
    """On Windows these are devices rather than files, whatever extension follows: a dataset
    named `nul` writes to the bit bucket, reads back empty, and every store reports success.
    The Linux image is unaffected, but the dev server runs on Windows, so the name has to be
    refused where it is validated -- one place -- and not at each of the stores."""
    with pytest.raises(HTTPException) as exc:
        safe_name(device)
    assert exc.value.status_code == 400


def test_safe_name_accepts_names_that_only_begin_like_a_device():
    """The check is the whole stem, not a prefix: refusing anything starting with `con` would
    cost `conference`, and only COM1-COM9 are devices -- `com10` is an ordinary name."""
    for good in ("conference", "nullable-labels", "com10", "auxiliary", "lpt"):
        assert safe_name(good) == good


def test_the_auth_module_does_not_pull_in_the_llm_module():
    """Answering a key check must not need the LLM client. Measured in a fresh interpreter
    rather than read off the import list: an AST walk misses a transitive path
    (security -> settings -> llm) while counting one guarded by `TYPE_CHECKING` that the
    interpreter never executes. `llm_override` is header parsing and lives beside the routes,
    which also keeps FastAPI out of `app/llm.py`."""
    probe = "import app.security, sys; print('app.llm' in sys.modules)"
    loaded = subprocess.run(
        [sys.executable, "-c", probe], capture_output=True, text=True, check=True
    )

    assert loaded.stdout.strip() == "False", "importing app.security loaded app.llm"
