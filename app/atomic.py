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
from collections.abc import Callable
from pathlib import Path


def replace_atomically(path: Path, write: Callable[[Path], object]) -> None:
    """Call ``write`` with a sibling temp path, then rename it over ``path``."""
    tmp = path.with_name(path.name + ".tmp")
    try:
        write(tmp)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def write_text_atomic(path: Path, text: str) -> None:
    """UTF-8 text through :func:`replace_atomically` -- the common case."""
    replace_atomically(path, lambda tmp: tmp.write_text(text, encoding="utf-8"))
