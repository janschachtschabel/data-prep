"""Refine dataset store: working CSVs under ``data_dir/refine/<name>.csv``.

Unlike references (M3), refine datasets are stored RAW — refine inspects data
as-is (PII handling is an explicit filter operation, not an import side effect).
"""

from __future__ import annotations

import json
import os
from collections.abc import Callable
from pathlib import Path

import pandas as pd

from ..security import safe_name
from ..settings import Settings
from ..tabular import read_table


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


def _replace_atomically(path: Path, write: Callable[[Path], None]) -> None:
    """Write through a sibling ``.tmp`` and rename it into place.

    A reader then sees the previous file or the complete new one, never a half
    written one -- the same pattern ``run_store`` and ``review`` use. Unlike
    those, a failed write is cleaned up here: a stray ``.tmp`` beside a dataset
    is a question nobody should have to answer later.
    """
    tmp = path.with_name(path.name + ".tmp")
    try:
        write(tmp)
        os.replace(tmp, path)
    finally:
        tmp.unlink(missing_ok=True)


def save_dataset(settings: Settings, name: str, df: pd.DataFrame) -> None:
    _replace_atomically(
        dataset_path(settings, name),
        lambda tmp: df.to_csv(tmp, sep=";", index=False, encoding="utf-8"),
    )


def list_datasets(settings: Settings) -> list[dict]:
    out = []
    for path in sorted(refine_dir(settings).glob("*.csv")):
        header = pd.read_csv(path, sep=";", nrows=0, encoding="utf-8")
        columns = list(header.columns)
        # Count DATA rows through the CSV parser, not raw lines: a quoted cell
        # may span several physical lines (refine stores uploads verbatim), so a
        # line count over-counts. Reading a single column keeps the cost bounded.
        rows = int(len(pd.read_csv(path, sep=";", usecols=[0], dtype=str, encoding="utf-8"))) if columns else 0
        out.append({"name": path.stem, "rows": rows, "columns": columns})
    return out


def delete_dataset(settings: Settings, name: str) -> bool:
    path = dataset_path(settings, name)
    existed = path.exists()
    path.unlink(missing_ok=True)
    _ops_path(settings, name).unlink(missing_ok=True)
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
    _replace_atomically(
        _ops_path(settings, name),
        lambda tmp: tmp.write_text(json.dumps(ops, ensure_ascii=False), encoding="utf-8"),
    )


def append_op(settings: Settings, name: str, record: dict) -> None:
    write_ops(settings, name, [*read_ops(settings, name), record])
