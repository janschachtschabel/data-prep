"""Refine dataset store: working CSVs under ``data_dir/refine/<name>.csv``.

Unlike references (M3), refine datasets are stored RAW — refine inspects data
as-is (PII handling is an explicit filter operation, not an import side effect).
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor
from functools import partial
from pathlib import Path

import pandas as pd

from ..atomic import replace_atomically, write_text_atomic
from ..security import safe_name
from ..settings import Settings
from ..tabular import read_table

# ONE thread for the store's files. Off the event loop, loading or saving a large
# table no longer stalls /health and run polling (up to 2.4 s for the 81 MB WLO
# export). One thread rather than the shared pool keeps what running on the loop
# gave for free: store work happens one step at a time, in the order it arrives.
# A check and the write it guards (see commit) stay one step no other request
# comes between, and a file is never replaced while another request reads it —
# which Windows refuses outright.
_store_thread = ThreadPoolExecutor(max_workers=1, thread_name_prefix="refine-store")


async def in_store[**P, T](fn: Callable[P, T], /, *args: P.args, **kwargs: P.kwargs) -> T:
    """Run ``fn`` — a read of the store, or a step that writes it — on the store's
    thread, and wait for it without blocking the event loop."""
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_store_thread, partial(fn, *args, **kwargs))


def commit(
    settings: Settings,
    tables: dict[str, tuple[pd.DataFrame, list[dict]]],
    *,
    guard: Callable[[], None] | None = None,
) -> None:
    """Check with ``guard``, then write each table and its history — as one step.

    Run it through :func:`in_store`: there nothing comes between the check and the
    writes, so a name another request took while this one computed is still
    refused. Each table is written before its history, so a crash between the two
    leaves a valid table whose provenance is merely behind."""
    if guard is not None:
        guard()
    for name, (df, ops) in tables.items():
        save_dataset(settings, name, df)
        write_ops(settings, name, ops)


def refine_dir(settings: Settings) -> Path:
    directory = settings.data_dir / "refine"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def dataset_path(settings: Settings, name: str) -> Path:
    return refine_dir(settings) / f"{safe_name(name, 'dataset name')}.csv"


def read_csv(raw: bytes, *, separator: str = ";") -> pd.DataFrame:
    """Parse a stored or uploaded CSV as all-strings.

    Thin delegate to :func:`app.tabular.read_table`, which owns every format
    this app reads. Kept as a named function because "read the store's CSV" is
    a different intent from "read whatever the operator uploaded".
    """
    return read_table(raw, fmt="csv", separator=separator)


def load_dataset(settings: Settings, name: str) -> pd.DataFrame | None:
    path = dataset_path(settings, name)
    if not path.exists():
        return None
    # keep_default_na=False mirrors the reader: an empty cell saved as ""
    # must load as "", and a cell holding the text "NA" must stay text.
    return pd.read_csv(path, sep=";", dtype=str, encoding="utf-8", keep_default_na=False)


def load_with_ops(settings: Settings, name: str) -> tuple[pd.DataFrame, list[dict]] | None:
    """The table and its history, or None when there is no such table.

    One function so that ONE store step reads both: read in two steps, a write
    queued in between paired the rows of one version with the history of the
    next -- and a result recorded provenance its rows never went through."""
    df = load_dataset(settings, name)
    return None if df is None else (df, read_ops(settings, name))


def stored_ops(settings: Settings, name: str) -> list[dict] | None:
    """The history of a stored table, or None when there is no such table --
    checked and read in one step, for the same reason as :func:`load_with_ops`."""
    return read_ops(settings, name) if dataset_path(settings, name).exists() else None


def save_dataset(settings: Settings, name: str, df: pd.DataFrame) -> None:
    replace_atomically(
        dataset_path(settings, name),
        lambda tmp: df.to_csv(tmp, sep=";", index=False, encoding="utf-8"),
    )
    # The shape beside the table, so listing never re-reads it. Written AFTER
    # the CSV: a crash between the two leaves a table that is merely counted
    # the slow way, never a shape without its table.
    shape = {"rows": int(len(df)), "columns": [str(c) for c in df.columns]}
    write_text_atomic(_meta_path(settings, name), json.dumps(shape, ensure_ascii=False))


def _meta_path(settings: Settings, name: str) -> Path:
    return refine_dir(settings) / f"{safe_name(name, 'dataset name')}.meta.json"


def _shape_by_parsing(path: Path) -> dict:
    """Rows and columns of a table stored before the sidecar existed.

    Counted through the CSV parser, not by lines: a quoted cell may span
    several physical lines (refine stores uploads verbatim). Reading a single
    column keeps the cost bounded -- but it is still a pass over the file,
    which is why saved tables record their shape instead."""
    try:
        columns = [str(c) for c in pd.read_csv(path, sep=";", nrows=0, encoding="utf-8").columns]
    except pd.errors.EmptyDataError:
        return {"rows": 0, "columns": []}
    rows = int(len(pd.read_csv(path, sep=";", usecols=[0], dtype=str, encoding="utf-8"))) if columns else 0
    return {"rows": rows, "columns": columns}


def list_datasets(settings: Settings) -> list[dict]:
    out = []
    for path in sorted(refine_dir(settings).glob("*.csv")):
        meta = path.with_suffix(".meta.json")
        try:
            shape = json.loads(meta.read_text(encoding="utf-8")) if meta.exists() else _shape_by_parsing(path)
        except json.JSONDecodeError:  # a hand-damaged sidecar must not hide the table
            shape = _shape_by_parsing(path)
        out.append({"name": path.stem, **shape})
    return out


def delete_dataset(settings: Settings, name: str) -> bool:
    path = dataset_path(settings, name)
    existed = path.exists()
    path.unlink(missing_ok=True)
    _ops_path(settings, name).unlink(missing_ok=True)
    _meta_path(settings, name).unlink(missing_ok=True)
    return existed


# Operation provenance. A CSV has no comment convention, so the operations log
# lives in a sidecar ``<name>.ops.json`` (a leading '#' comment line would break
# downstream CSV readers) — a deliberate, documented deviation from the plan's
# "header comment".


def _ops_path(settings: Settings, name: str) -> Path:
    return refine_dir(settings) / f"{safe_name(name, 'dataset name')}.ops.json"


def read_ops(settings: Settings, name: str) -> list[dict]:
    path = _ops_path(settings, name)
    if not path.exists():
        return []
    return json.loads(path.read_text(encoding="utf-8"))


def write_ops(settings: Settings, name: str, ops: list[dict]) -> None:
    write_text_atomic(_ops_path(settings, name), json.dumps(ops, ensure_ascii=False))


def append_op(settings: Settings, name: str, record: dict) -> None:
    write_ops(settings, name, [*read_ops(settings, name), record])
