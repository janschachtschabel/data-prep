"""Guarded HTTPS JSON fetch — the ONE deliberate deviation from api_v3's
no-URL-fetch rule (documented in the design doc).

Guards, in order: https-only scheme, host allowlist, no redirects, streamed
size cap, timeout, JSON-object body. Every rejection raises :class:`FetchError`
whose message is safe to show to API clients.
"""

from __future__ import annotations

import json
from urllib.parse import urlparse

import httpx

from .settings import Settings


class FetchError(ValueError):
    """Fetch rejected or failed; the message is client-safe."""


def fetch_json(url: str, settings: Settings, transport: httpx.BaseTransport | None = None) -> dict:
    """Fetch ``url`` under the configured guards and return the parsed JSON object.

    ``transport`` exists as a test seam (httpx.MockTransport) — production
    callers never pass it.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise FetchError("Only https:// URLs are allowed.")
    host = (parsed.hostname or "").lower()
    allowed = settings.fetch_allowed_hosts_list
    if host not in allowed:
        raise FetchError(f"Host {host!r} is not allowed. Allowed hosts: {', '.join(allowed)}.")

    cap = settings.fetch_max_mb * 1024 * 1024
    body = bytearray()
    try:
        with httpx.Client(
            transport=transport, timeout=settings.fetch_timeout_seconds, follow_redirects=False
        ) as client, client.stream("GET", url) as response:
            if response.is_redirect:
                raise FetchError("Redirects are not followed — use the final URL.")
            response.raise_for_status()
            for chunk in response.iter_bytes():
                body += chunk
                if len(body) > cap:
                    raise FetchError(f"Response exceeds the {settings.fetch_max_mb} MB limit.")
    except httpx.HTTPStatusError as exc:
        raise FetchError(f"Fetch failed with HTTP {exc.response.status_code}.") from exc
    except httpx.HTTPError as exc:
        # Class name only (timeout, connect error, ...) — no internals leak.
        raise FetchError(f"Fetch failed: {exc.__class__.__name__}.") from exc

    try:
        data = json.loads(bytes(body).decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise FetchError("Response is not valid JSON.") from exc
    if not isinstance(data, dict):
        raise FetchError("Response is not a JSON object.")
    return data
