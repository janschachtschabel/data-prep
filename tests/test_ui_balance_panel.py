"""The balance panel's behaviour, checked by running the real UI scripts in node.

The markup tests in test_ui.py cannot see what happens after a click: whether a
finished run re-arms the run button, whether a stale plan can still be run. Those
were real findings (review 2026-09-16, #9 and #18), so they are pinned here. The
check needs node; without it the test is skipped rather than silently passing.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest

CHECK = Path(__file__).parent / "ui" / "balance_panel.check.js"


@pytest.mark.skipif(shutil.which("node") is None, reason="node is not installed")
def test_the_balance_panel_arms_and_disarms_as_it_should():
    result = subprocess.run(
        ["node", str(CHECK)], capture_output=True, text=True, timeout=60, check=False,
    )
    assert result.returncode == 0, result.stdout + result.stderr
