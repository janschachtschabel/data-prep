"""The table export controls' behaviour, checked by running the real UI scripts in node.

`spreadsheet_safe` is a CSV property; for JSON and JSONL the download ignores it. The
markup tests in test_ui.py see the checkbox but not what a format change does to it, so
that it goes dead for those formats is pinned here (review of the merged branches,
2026-09-20). The check needs node; without it the test is skipped rather than silently
passing.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

CHECK = Path(__file__).parent / "ui" / "tables_export.check.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_spreadsheet_checkbox_goes_dead_for_the_formats_that_ignore_it():
    result = subprocess.run(
        ["node", str(CHECK)], capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
