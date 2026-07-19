"""Tolerant reader for a run's ``samples.jsonl``.

A crash or a killed process can leave a blank or partial line behind; a single
such line must never take down a whole read — not the resume rebuild, not an
export, not the review browser. Every consumer of ``samples.jsonl`` goes through
here so the skip-corrupted rule lives in exactly one place.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path

logger = logging.getLogger("data_prep.samples")


def read_samples(path: Path) -> list[dict]:
    """All parseable JSON objects in a ``samples.jsonl`` file, in file order.

    Blank and corrupted lines are skipped (and the count logged); a missing file
    yields an empty list. Losing one partial line beats failing the whole read.
    """
    if not path.exists():
        return []
    samples: list[dict] = []
    corrupted = 0
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line:
            continue
        try:
            samples.append(json.loads(line))
        except json.JSONDecodeError:
            corrupted += 1
    if corrupted:
        logger.warning("Skipped %d corrupted line(s) in %s", corrupted, path)
    return samples
