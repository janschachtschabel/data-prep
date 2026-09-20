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
import io
import json
import zlib

import pandas as pd

from .spreadsheet import spreadsheet_csv

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

# Ceiling on what a gzip payload may inflate to. The upload cap bounds the
# COMPRESSED size only, and gzip reaches 1000:1 on repetitive input, so an upload
# of zeros at the cap would otherwise inflate a thousandfold inside the single
# worker. Callers that know the upload cap pass a multiple of it instead — the
# multiple has to stay above what real data does (a WLO export inflates ~11x),
# so it is the bomb it stops, not the export.
DEFAULT_MAX_DECOMPRESSED = 256 * 1024 * 1024


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

    Raises ``ValueError`` when a key containing a dot collides with a nested
    path, since the two would otherwise share one column.
    """
    out: dict[str, str] = {}

    def walk(node: object, prefix: str) -> None:
        if isinstance(node, dict) and node:
            for key, value in node.items():
                walk(value, f"{prefix}.{key}" if prefix else str(key))
        else:
            if prefix in out:
                # Only a key that itself contains "." can reach a path twice,
                # e.g. {"a.b": 1, "a": {"b": 2}}. Last-wins would lose a value
                # silently; naming the column lets the operator fix the source.
                raise ValueError(
                    f"Ambiguous column {prefix!r}: a key containing a dot collides "
                    f"with a nested path."
                )
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
        return f"{_sniff_text(_peek_gzip(raw))}.gz"
    return _sniff_text(raw)


def _peek_gzip(raw: bytes) -> bytes:
    """The first uncompressed bytes of a gzip stream, without the whole stream.

    ``gzip.decompress`` demands a COMPLETE stream and raises on a slice, so a
    truncated peek failed for every real-sized file and the sniff fell back to
    CSV. A streaming decompressor yields what it can from the bytes it is given.
    A genuinely corrupt file still surfaces in the reader with its own error.
    """
    try:
        # 16 + MAX_WBITS: accept the gzip header, not a bare zlib stream.
        return zlib.decompressobj(16 + zlib.MAX_WBITS).decompress(raw[: _PEEK * 4], _PEEK)
    except zlib.error:
        return b""


def _decompress(raw: bytes, max_bytes: int) -> bytes:
    """Inflate a gzip payload, refusing past ``max_bytes`` instead of trying.

    A streaming decompressor stops at the ceiling rather than allocating the
    whole result first -- the point is never to hold the bomb in memory.
    """
    inflater = zlib.decompressobj(16 + zlib.MAX_WBITS)
    try:
        # One byte past the ceiling is enough to tell "exactly at" from "over".
        out = inflater.decompress(raw, max_bytes + 1)
    except zlib.error as exc:
        raise ValueError(f"File is not readable gzip: {exc}.") from exc
    if len(out) > max_bytes or inflater.unconsumed_tail:
        raise ValueError(
            f"Decompressed file is larger than the {max_bytes // (1024 * 1024)} MB ceiling."
        )
    if not inflater.eof:
        raise ValueError("File is not readable gzip: the stream ends early.")
    return out


def _records_to_frame(records: list[dict], list_separator: str) -> pd.DataFrame:
    """Flattened records into an all-string frame with the union of all keys.

    Two exports rarely carry identical fields, so a key missing from one record
    becomes an empty cell -- dropping the row or the column instead would lose
    data the operator can still see and filter.
    """
    flat = [flatten_record(record, list_separator=list_separator) for record in records]
    return pd.DataFrame(flat, dtype=str).fillna("")


def _read_json(text: str, list_separator: str) -> pd.DataFrame:
    try:
        payload = json.loads(text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"File is not readable JSON: line {exc.lineno}, {exc.msg}.") from exc
    if isinstance(payload, dict):
        payload = [payload]
    if not isinstance(payload, list):
        raise ValueError("JSON must be an object or an array of objects.")
    non_objects = [i for i, item in enumerate(payload) if not isinstance(item, dict)]
    if non_objects:
        raise ValueError(f"JSON array must hold objects; item {non_objects[0]} is not one.")
    return _records_to_frame(payload, list_separator)


def _read_jsonl(text: str, list_separator: str) -> pd.DataFrame:
    records = []
    for number, line in enumerate(text.splitlines(), start=1):
        stripped = line.strip()
        if not stripped:
            continue  # blank lines are padding in every export we have seen
        try:
            record = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise ValueError(f"File is not readable JSONL: line {number}, {exc.msg}.") from exc
        if not isinstance(record, dict):
            raise ValueError(f"JSONL line {number} is not an object.")
        records.append(record)
    return _records_to_frame(records, list_separator)


def _read_csv(raw: bytes, separator: str, encoding: str) -> pd.DataFrame:
    try:
        # keep_default_na=False keeps the all-strings promise: without it an
        # empty cell becomes NaN (a float) and the literal strings "NA"/"null"
        # become missing values -- both wrong here, where a keyword or taxonid
        # may legitimately BE the text "NA".
        return pd.read_csv(
            io.BytesIO(raw), sep=separator, dtype=str,
            encoding=encoding, keep_default_na=False,
        )
    except pd.errors.EmptyDataError:
        # An empty file is not a PARSE failure; let the caller's shape check
        # report "no data rows", which is what actually went wrong.
        return pd.DataFrame()
    except (ValueError, UnicodeDecodeError) as exc:
        raise ValueError(
            f"File is not a readable CSV (separator {separator!r}, {encoding}): "
            f"{exc.__class__.__name__}."
        ) from exc


def read_table(
    raw: bytes,
    *,
    fmt: str = "auto",
    separator: str = ";",
    encoding: str = "utf-8",
    list_separator: str = ",",
    filename: str = "",
    max_bytes: int = DEFAULT_MAX_DECOMPRESSED,
) -> pd.DataFrame:
    """Parse an uploaded payload into an all-string DataFrame.

    ``fmt`` is one of :data:`SUPPORTED_READ`, or ``"auto"`` to derive it from
    ``filename`` and the bytes. Nested JSON is always flattened to dot-path
    columns -- see :func:`flatten_record`.

    ``max_bytes`` caps what a gzip payload may inflate to; see
    :data:`DEFAULT_MAX_DECOMPRESSED` for why that ceiling exists.

    Raises ``ValueError`` with a client-safe message on anything unreadable.
    """
    resolved = sniff_format(filename, raw) if fmt == "auto" else fmt
    if resolved not in SUPPORTED_READ:
        raise ValueError(
            f"Unsupported format {resolved!r}. Supported: {', '.join(SUPPORTED_READ)}."
        )

    payload = _decompress(raw, max_bytes) if resolved.endswith(".gz") else raw
    base = resolved.removesuffix(".gz")

    if base == "csv":
        frame = _read_csv(payload, separator, encoding)
    else:
        try:
            text = payload.decode(encoding)
        except UnicodeDecodeError as exc:
            raise ValueError(f"File is not readable {encoding} text: {exc.reason}.") from exc
        frame = _read_json(text, list_separator) if base == "json" else _read_jsonl(text, list_separator)

    if frame.shape[1] == 0 or frame.shape[0] == 0:
        raise ValueError("File has no data rows or no columns.")
    return frame


def write_table(df: pd.DataFrame, *, fmt: str = "csv", separator: str = ";",
                spreadsheet_safe: bool = False) -> bytes:
    """Serialize a frame into one of :data:`SUPPORTED_WRITE`.

    UTF-8 and Unix line endings throughout, so a file written on Windows and
    one written in the Linux container hash the same.

    ``spreadsheet_safe`` writes the CSV formats so that no cell runs as a
    formula in Excel or LibreOffice (:mod:`app.spreadsheet`) -- the variant for
    a person, not for api_v3, which would train on the defusing apostrophe.
    JSON and JSONL are unaffected: no spreadsheet reads a formula out of them.

    Raises ``ValueError`` for an unsupported format.
    """
    if fmt not in SUPPORTED_WRITE:
        raise ValueError(
            f"Unsupported format {fmt!r}. Supported: {', '.join(SUPPORTED_WRITE)}."
        )
    frame = df.fillna("").astype(str)

    if fmt.startswith("csv"):
        text = (spreadsheet_csv(frame, sep=separator) if spreadsheet_safe
                else frame.to_csv(sep=separator, index=False, lineterminator="\n"))
    elif fmt == "json":
        text = json.dumps(frame.to_dict("records"), ensure_ascii=False, indent=2)
    else:
        text = "".join(
            json.dumps(record, ensure_ascii=False) + "\n"
            for record in frame.to_dict("records")
        )

    raw = text.encode("utf-8")
    # mtime=0 keeps the output byte-identical between runs, so a re-export can
    # be compared by hash instead of by parsing it again.
    return gzip.compress(raw, mtime=0) if fmt.endswith(".gz") else raw
