"""Bytes to DataFrame and back, for the formats operators actually hand over.

The refine store keeps ONE internal shape — semicolon CSV, UTF-8, every cell a
string — so that an operation never has to know which format a dataset arrived
in. Format handling therefore lives at the edges (import, download) and in this
module alone.

Nested JSON is flattened to dot-path columns rather than queried with a path
language: a WLO edu-sharing export wraps every property in a list, so
``properties.cclom:title`` is what a user wants to filter on, and that is a
column name, not an expression.
"""

from __future__ import annotations

import gzip
import json

SUPPORTED_READ = ("csv", "csv.gz", "json", "jsonl", "jsonl.gz")
SUPPORTED_WRITE = ("csv", "csv.gz", "json", "jsonl")

# Gzip's magic number. Compression has to be read from the bytes rather than
# the name: a mislabelled ".csv" that is really gzipped would otherwise be
# parsed as text and fail with an unreadable encoding error.
_GZIP_MAGIC = b"\x1f\x8b"

_EXTENSIONS = {
    ".csv.gz": "csv.gz",
    ".jsonl.gz": "jsonl.gz",
    ".ndjson.gz": "jsonl.gz",
    ".json.gz": "json.gz",
    ".csv": "csv",
    ".jsonl": "jsonl",
    ".ndjson": "jsonl",
    ".json": "json",
}

# Enough bytes to see a JSON opening bracket past any leading whitespace,
# without decompressing a whole export just to name its format.
_PEEK = 4096


def _scalar(value: object, list_separator: str) -> str:
    """One JSON value as one cell.

    JSON spelling is kept deliberately (``true`` rather than Python's ``True``)
    because that is what the source document said and what a user filtering the
    column will type.
    """
    if value is None:
        return ""
    if isinstance(value, str):
        return value
    if isinstance(value, bool):
        return "true" if value else "false"
    if isinstance(value, (int, float)):
        return json.dumps(value)
    if isinstance(value, list):
        if not value:
            return ""
        # A list of objects has no sensible column of its own. Keeping it as
        # JSON text is a documented limitation -- the alternative would be to
        # drop it silently, which is worse.
        if any(isinstance(item, (dict, list)) for item in value):
            return json.dumps(value, ensure_ascii=False)
        return list_separator.join(_scalar(item, list_separator) for item in value)
    if isinstance(value, dict):
        # Only ever reached for an EMPTY dict -- flatten_record descends into
        # every non-empty one. Empty in, empty cell out, like the empty list.
        return ""
    return json.dumps(value, ensure_ascii=False)


def flatten_record(obj: dict, *, list_separator: str = ",") -> dict[str, str]:
    """One nested JSON object as one flat row of string cells.

    Nested objects become dot paths (``properties.cclom:title``); lists of
    scalars are joined; lists of objects survive as JSON text. An empty object
    or list yields an empty cell rather than disappearing, so the column still
    exists and the row count never depends on how full a record was.
    """
    out: dict[str, str] = {}

    def walk(node: object, prefix: str) -> None:
        if isinstance(node, dict) and node:
            for key, value in node.items():
                walk(value, f"{prefix}.{key}" if prefix else str(key))
        else:
            out[prefix] = _scalar(node, list_separator)

    walk(obj, "")
    return out


def _sniff_text(raw: bytes) -> str:
    """Name an uncompressed payload from its first non-blank character."""
    head = raw[:_PEEK].lstrip()
    if head.startswith(b"["):
        return "json"
    if head.startswith(b"{"):
        # One object per line is JSONL; a single pretty-printed object spanning
        # lines is JSON. A second '{' after a newline settles it.
        return "jsonl" if b"\n{" in head else "json"
    return "csv"


def sniff_format(filename: str, raw: bytes) -> str:
    """Best guess at a payload's format.

    Compression is decided by the bytes (gzip's magic number), the inner format
    by the extension where it is known and by the content otherwise.
    """
    lowered = (filename or "").lower()
    compressed = raw.startswith(_GZIP_MAGIC)

    for extension, fmt in _EXTENSIONS.items():
        if lowered.endswith(extension):
            if compressed and not fmt.endswith(".gz"):
                return f"{fmt}.gz"
            return fmt

    if compressed:
        try:
            inner = gzip.decompress(raw[: _PEEK * 16])
        except (OSError, EOFError):
            # A truncated peek is normal -- we only need the first bytes, and
            # the reader will report a genuinely corrupt file with its own error.
            inner = b""
        return f"{_sniff_text(inner)}.gz"
    return _sniff_text(raw)
