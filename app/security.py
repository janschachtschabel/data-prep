"""Authentication and input-safety helpers.

- Single API-key auth (one operator, no roles), compared in constant time to
  avoid timing side channels. No configured key = auth disabled, but only for
  loopback clients — a keyless instance never serves the network wide open.
- ``safe_name`` rejects path traversal in user-supplied names.
"""

from __future__ import annotations

import ipaddress
import secrets

from fastapi import Depends, Header, HTTPException, Request, Security, UploadFile
from fastapi.security import APIKeyHeader

from .llm import LlmOverride
from .settings import Settings, get_settings

api_key_header = APIKeyHeader(name="X-API-Key", auto_error=False)


def llm_override(
    x_llm_key: str | None = Header(default=None, alias="X-LLM-Key"),
    x_llm_model: str | None = Header(default=None, alias="X-LLM-Model"),
) -> LlmOverride:
    """Extract per-request LLM credentials from headers (open-instance mode).

    ``X-LLM-Key`` / ``X-LLM-Model`` let a caller drive the LLM with their own
    key + model without any server-wide env key. Both are optional; empty
    strings collapse to ``None`` so the endpoint falls back to config + env.
    The key is a secret — it lives only for the duration of the request (and,
    for a background run, in memory), never persisted or logged.
    """
    return LlmOverride(api_key=x_llm_key or None, model=x_llm_model or None)


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


# The same cap every request-body name field carries (Field(max_length=100)).
# Path parameters have no pydantic bound, so it is enforced here: a name over
# 255 bytes makes Path.exists() raise ENAMETOOLONG on Linux, i.e. a 500.
MAX_NAME_LENGTH = 100


def safe_name(name: str, kind: str = "name") -> str:
    """Validate a user-supplied name, rejecting path-traversal characters and
    over-long names."""
    if len(name) > MAX_NAME_LENGTH:
        raise HTTPException(
            status_code=400, detail=f"Invalid {kind}: longer than {MAX_NAME_LENGTH} characters."
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
    return name


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
