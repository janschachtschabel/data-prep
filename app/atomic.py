"""Atomic file replacement -- the one place every store's write goes through.

A reader sees the previous file or the complete new one, never a half-written
one: the content goes to a sibling ``.tmp`` and ``os.replace`` swaps it in. A
failed write is cleaned up, so a stray ``.tmp`` beside a dataset is never a
question anybody has to answer later.

Per-file atomicity only. A store that writes two files (a CSV plus its meta)
gets each file whole; the pair is ordered by the caller so the residual crash
window is a valid state.
"""

from __future__ import annotations

import os
import secrets
from collections.abc import Callable
from pathlib import Path


def replace_atomically(path: Path, write: Callable[[Path], object]) -> None:
    """Call ``write`` with a sibling temp path, then rename it over ``path``.

    The temp name is unique per call: with one fixed name per target, two
    writers to the same name (the startup reference import and an upload,
    both in threads) shared a temp file, and the first writer's rename found
    it gone. Eight hex characters keep the longest name within NAME_MAX (see
    ``security.MAX_NAME_BYTES``)."""
    tmp = path.with_name(f"{path.name}.{secrets.token_hex(4)}.tmp")
    try:
        write(tmp)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def sweep_stale_temps(*directories: Path) -> int:
    """Delete ``*.tmp`` files below ``directories``; returns how many.

    A write killed mid-way (power loss, OOM kill) leaves its uniquely named
    temp file behind for good -- the next write picks a new name. Call this at
    startup only, when no write can be in flight (single-worker design)."""
    removed = 0
    for directory in directories:
        if not directory.exists():
            continue
        for path in directory.rglob("*.tmp"):
            if path.is_file():
                path.unlink(missing_ok=True)
                removed += 1
    return removed


def write_text_atomic(path: Path, text: str) -> None:
    """UTF-8 text through :func:`replace_atomically` -- the common case."""
    replace_atomically(path, lambda tmp: tmp.write_text(text, encoding="utf-8"))
