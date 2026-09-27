"""Authentication and input-safety helpers.

- Single API-key auth (one operator, no roles), compared in constant time to
  avoid timing side channels. No configured key = auth disabled, but only for
  loopback clients — a keyless instance never serves the network wide open.
- ``safe_name`` rejects path traversal in user-supplied names.
"""

from __future__ import annotations

import ipaddress
import secrets

from fastapi import Depends, HTTPException, Request, Security, UploadFile
from fastapi.security import APIKeyHeader

from .settings import Settings, get_settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def require_key(
    request: Request,
    key: str | None = Security(api_key_header),
    settings: Settings = Depends(get_settings),
) -> None:
    """FastAPI dependency: enforce the configured API key.

    With no key configured, auth is disabled as a LOCAL convenience — but only
    for loopback clients. A keyless instance reached from a non-loopback address
    is network-exposed with no auth, so such requests fail closed (403) instead
    of being served wide open. Behind a reverse proxy ``request.client`` is the
    proxy, so a key must be configured for any remote use."""
    if not settings.auth_enabled:
        if _is_loopback_client(request):
            return
        raise HTTPException(
            status_code=403,
            detail="No API key configured: this instance serves loopback clients "
            "only. Set DATAPREP_AUTH_KEY to enable remote access.",
        )
    # Compared as bytes: header values arrive latin-1 decoded, and compare_digest
    # refuses non-ASCII str -- a key with an umlaut must be a 401, not a 500.
    if (
        key is None
        or settings.auth_key is None
        or not secrets.compare_digest(key.encode("utf-8"), settings.auth_key.encode("utf-8"))
    ):
        raise HTTPException(
            status_code=401,
            detail="API key required. Provide the X-API-Key header.",
            headers={"WWW-Authenticate": "ApiKey"},
        )


def _is_loopback_client(request: Request) -> bool:
    """True when the request peer is a loopback address (127.0.0.0/8 or ::1).

    A missing client (in-process ASGI call) counts as loopback; an unparseable
    host (e.g. a proxy hostname) counts as non-loopback — behind a proxy the
    operator must configure a key."""
    client = request.client
    if client is None:
        return True
    try:
        return ipaddress.ip_address(client.host).is_loopback
    except ValueError:
        return False


# Counted in UTF-8 BYTES, because that is what the filesystem counts: Linux
# refuses a file name over 255 bytes (ENAMETOOLONG, a 500). The stores append up
# to 23 bytes (".meta.json" plus atomic.py's ".<8 hex>.tmp"), so 200 leaves room.
# A character cap got it wrong both ways: it refused the longer names split
# derives from a target, and let 70 emoji (280 bytes) through. Every body field
# that carries a name uses this as its character cap; this check is the real one.
MAX_NAME_BYTES = 200

# Windows resolves these to devices rather than files, with or without an extension: a
# dataset named `nul` writes to the bit bucket, reads back empty, and every store reports
# success. The Linux image is unaffected — this is for the dev server, which runs on Windows.
# COM10 and up are ordinary names; only the single digits are devices.
_WINDOWS_DEVICES = frozenset(
    {"CON", "PRN", "AUX", "NUL", "CONIN$", "CONOUT$"}
    | {f"COM{digit}" for digit in "123456789"}
    | {f"LPT{digit}" for digit in "123456789"}
)


def _is_windows_device(name: str) -> bool:
    """Whether Windows would read this name as a device.

    The comparison is the stem — everything before the first dot — because `nul.csv` is the
    same device as `nul`, and trailing spaces and dots are ignored by the Win32 path parser,
    so `nul ` and `nul.` are too. Matching a prefix instead would cost `conference`.
    """
    stem = name.split(".", 1)[0].rstrip(" ")
    return stem.upper() in _WINDOWS_DEVICES


def safe_name(name: str, kind: str = "name") -> str:
    """Validate a user-supplied name, rejecting path-traversal characters, names too long
    for a file name, and names Windows reads as a device."""
    if len(name.encode("utf-8")) > MAX_NAME_BYTES:
        raise HTTPException(
            status_code=400, detail=f"Invalid {kind}: longer than {MAX_NAME_BYTES} bytes."
        )
    if (
        not name
        or ".." in name
        or "/" in name
        or "\\" in name
        or name.startswith(".")
        or "\x00" in name
    ):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {kind}: {name!r}. Must not contain path characters (/, \\, ..).",
        )
    if _is_windows_device(name):
        raise HTTPException(
            status_code=400,
            detail=f"Invalid {kind}: {name!r} is a reserved device name on Windows.",
        )
    return name


def refuse_existing(exists: bool, kind: str, name: str, overwrite: bool) -> None:
    """409 when the name is taken and the caller did not ask to replace it.

    Every store used to overwrite silently: a target name typed twice destroyed
    another dataset, a rebuilt seed set lost its hand-edited and LLM-paid seeds.
    api_v3 refuses an existing name the same way; ``overwrite`` is the explicit
    "yes, replace it" the UI sends after asking."""
    if exists and not overwrite:
        raise HTTPException(
            status_code=409, detail=f"{kind} {name!r} already exists. Send overwrite=true to replace it."
        )


async def read_upload_capped(upload: UploadFile, max_bytes: int) -> bytes:
    """Read an uploaded file in chunks, aborting if it exceeds ``max_bytes``."""
    chunks: list[bytes] = []
    total = 0
    while True:
        chunk = await upload.read(1024 * 1024)
        if not chunk:
            break
        total += len(chunk)
        if total > max_bytes:
            raise HTTPException(
                status_code=413, detail=f"Upload exceeds {max_bytes // (1024 * 1024)} MB limit."
            )
        chunks.append(chunk)
    return b"".join(chunks)
