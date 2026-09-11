"""Reference sets (the OPTIONAL hybrid-mode input): CSV ingest with input PII
scrub and per-concept grouping.

The hard guarantee: the raw upload is scrubbed IN MEMORY (mask policy) before
anything is written — plaintext PII never reaches disk. Reference data feeds
seed distillation and the leakage index only; the exporter never reads it
(separation guarantee, pinned by test when the exporter lands).
"""

from __future__ import annotations

import io
import json
import logging
from pathlib import Path

import pandas as pd

from .atomic import replace_atomically, write_text_atomic
from .config import DefaultReference
from .pii import PiiReport, scrub
from .security import safe_name
from .settings import _BASE, Settings
from .textnorm import split_labels

logger = logging.getLogger("data_prep.reference")

# WLO export convention (verified against the real data_30k.csv header) —
# overridable per upload for differently shaped files.
DEFAULT_TEXT_COLUMNS = (
    "properties.cclom:title",
    "properties.cclom:general_description",
    "properties.cclom:general_keyword",
)
DEFAULT_LABEL_COLUMN = "properties.ccm:taxonid"


def references_dir(settings: Settings) -> Path:
    directory = settings.data_dir / "references"
    directory.mkdir(parents=True, exist_ok=True)
    return directory


def load_reference(settings: Settings, name: str) -> tuple[pd.DataFrame, dict] | None:
    """Load a stored (already scrubbed) reference set, or ``None`` if missing."""
    name = safe_name(name, "reference name")
    base = references_dir(settings)
    csv_path, meta_path = base / f"{name}.csv", base / f"{name}.meta.json"
    if not (csv_path.exists() and meta_path.exists()):
        return None
    df = pd.read_csv(csv_path, sep=";", dtype=str, encoding="utf-8").fillna("")
    meta = json.loads(meta_path.read_text(encoding="utf-8"))
    return df, meta


def store_reference(settings: Settings, name: str, df: pd.DataFrame, meta: dict) -> None:
    """Write a (scrubbed) reference set: the CSV first, then the meta file that
    makes it listable -- so a crash between the two leaves an unlisted CSV, not
    a listing that points at a missing table. Each file lands whole."""
    base = references_dir(settings)
    replace_atomically(base / f"{name}.csv", lambda tmp: df.to_csv(tmp, sep=";", index=False, encoding="utf-8"))
    write_text_atomic(base / f"{name}.meta.json", json.dumps(meta, ensure_ascii=False))


def ingest_reference(
    raw: bytes,
    *,
    name: str,
    text_columns: tuple[str, ...] = DEFAULT_TEXT_COLUMNS,
    label_column: str = DEFAULT_LABEL_COLUMN,
) -> tuple[pd.DataFrame, dict]:
    """Parse, scrub and group an uploaded reference CSV (semicolon, UTF-8).

    Returns the scrubbed frame plus a JSON-safe meta dict (columns, PII report,
    per-concept group counts). Raises ``ValueError`` with a client-safe message
    on unparseable files or missing columns.
    """
    try:
        # UnicodeDecodeError and pandas parser errors are all ValueError subclasses.
        df = pd.read_csv(io.BytesIO(raw), sep=";", dtype=str, encoding="utf-8")
    except ValueError as exc:
        raise ValueError(
            f"File is not a readable CSV (semicolon-separated, UTF-8): {exc.__class__.__name__}."
        ) from exc

    missing = [col for col in (*text_columns, label_column) if col not in df.columns]
    if missing:
        raise ValueError(f"Missing required columns: {', '.join(missing)}.")

    # Input scrub, cell by cell; the label column stays untouched (URIs would
    # otherwise be masked as URLs).
    report = PiiReport()
    for idx in df.index:
        row_found: dict[str, int] = {}
        for col in text_columns:
            value = df.at[idx, col]
            if isinstance(value, str) and value:
                cleaned, found = scrub(value)
                if found:
                    df.at[idx, col] = cleaned
                    for category, count in found.items():
                        row_found[category] = row_found.get(category, 0) + count
        report.record(row_found)

    group_counts: dict[str, int] = {}
    for cell in df[label_column].fillna(""):
        for uri in split_labels(cell):
            group_counts[uri] = group_counts.get(uri, 0) + 1

    meta = {
        "name": name,
        "row_count": int(len(df)),
        "text_columns": list(text_columns),
        "label_column": label_column,
        "pii": report.as_dict(),
        "group_counts": group_counts,
    }
    return df, meta


def _resolve_path(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else (_BASE / p).resolve()


def seed_default_references(settings: Settings, defaults: list[DefaultReference]) -> list[str]:
    """Import configured default reference CSVs into the store (PII-scrubbed) if
    not already present. Idempotent; a missing/broken source is skipped, not
    fatal. Returns the names actually added. Config-driven — nothing hardcoded."""
    base = references_dir(settings)
    added: list[str] = []
    for entry in defaults:
        try:
            name = safe_name(entry.name, "reference name")
        except Exception:  # noqa: BLE001 - a bad configured name must not crash startup
            logger.warning("Skipping default reference with invalid name %r", entry.name)
            continue
        if (base / f"{name}.meta.json").exists():
            continue  # already imported
        source = _resolve_path(entry.path)
        if not source.exists():
            logger.info("Default reference %r: source %s not found — skipped", name, source)
            continue
        try:
            df, meta = ingest_reference(source.read_bytes(), name=name)
            store_reference(settings, name, df, meta)
        except (ValueError, OSError) as exc:
            logger.warning("Default reference %r failed to import: %s", name, exc)
            continue
        logger.info("Imported default reference %r (%d rows, PII-scrubbed)", name, meta["row_count"])
        added.append(name)
    return added
