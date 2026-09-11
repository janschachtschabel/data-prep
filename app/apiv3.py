"""Guarded push of a CSV to the configured api_v3 instance — shared by the run
exporter and the refine toolbox so both use the SAME guards.

Guard: the target host must be on the fetch allowlist or localhost (dev). The
API key comes from the env variable NAMED in config.yaml (never a literal key).
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

import httpx

from .config import load_config
from .settings import Settings

_LOCAL_HOSTS = ("127.0.0.1", "localhost")

# Test seam: tests set this to an httpx.MockTransport so no real network call
# happens.
_test_transport: httpx.BaseTransport | None = None


class PushError(Exception):
    """Push rejected or failed; ``status`` is the HTTP status a route should use."""

    def __init__(self, detail: str, status: int = 400) -> None:
        super().__init__(detail)
        self.detail = detail
        self.status = status


def _reason(response: httpx.Response) -> str:
    """api_v3's own ``detail`` for a refused request, as ": <detail>" (or "").

    Relayed because api_v3 is the operator's own configured host and its reason
    -- a name too long, a key without admin rights -- is what the operator has
    to act on; a bare status code is not. Capped, and read defensively: a proxy
    in between may answer with HTML."""
    try:
        detail = response.json().get("detail")
    except (ValueError, AttributeError):
        return ""
    return f": {str(detail)[:300].rstrip('.')}" if detail else ""


def _target(settings: Settings) -> tuple[str, str]:
    """The configured api_v3 base URL (no trailing slash) and its key.

    One guard for both callers: unconfigured, disallowed host or missing key
    each raise :class:`PushError` with status 400 before any network call."""
    target = load_config(settings.config_file).api_v3
    if not target.url:
        raise PushError("api_v3 is not configured (config.yaml: api_v3.url).")
    host = (urlparse(target.url).hostname or "").lower()
    if host not in settings.fetch_allowed_hosts_list and host not in _LOCAL_HOSTS:
        raise PushError(f"api_v3 host {host!r} is not allowed.")
    key = os.environ.get(target.api_key_env)
    if not key:
        raise PushError(f"Environment variable {target.api_key_env!r} is not set for the api_v3 key.")
    return target.url.rstrip("/"), key


async def push_csv(settings: Settings, csv_text: str, filename: str) -> dict:
    """Upload ``csv_text`` to ``<api_v3.url>/datasets/import``; returns api_v3's
    JSON. Raises :class:`PushError` (400 guard/config, 502 upstream)."""
    base_url, key = _target(settings)
    try:
        async with httpx.AsyncClient(
            transport=_test_transport, timeout=settings.fetch_timeout_seconds  # type: ignore[arg-type]
        ) as client:
            response = await client.post(
                base_url + "/datasets/import",
                headers={"X-API-Key": key},
                files={"file": (filename, csv_text.encode("utf-8"), "text/csv")},
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as exc:
        if exc.response.status_code == 409:
            # api_v3 never overwrites a dataset. A taken name is a conflict the
            # operator resolves, not a broken upstream -- say which and how.
            raise PushError(
                f"api_v3 already has a dataset named {filename!r}; delete it there first.", 409,
            ) from exc
        raise PushError(
            f"api_v3 rejected the upload (HTTP {exc.response.status_code}){_reason(exc.response)}.", 502,
        ) from exc
    except httpx.HTTPError as exc:
        raise PushError(f"api_v3 push failed: {exc.__class__.__name__}.", 502) from exc


async def predict_batch(
    settings: Settings, texts: list[str], model_name: str, *, top_k: int = 3, batch: int = 500
) -> list[list[dict]]:
    """Ranked predictions per text from the configured api_v3 model — used by the
    label audit for a second opinion. Same guards as :func:`push_csv`."""
    base_url, key = _target(settings)
    out: list[list[dict]] = []
    try:
        async with httpx.AsyncClient(
            transport=_test_transport, timeout=settings.fetch_timeout_seconds  # type: ignore[arg-type]
        ) as client:
            for start in range(0, len(texts), batch):
                response = await client.post(
                    base_url + "/predict/batch",
                    headers={"X-API-Key": key, "Content-Type": "application/json"},
                    json={"texts": texts[start:start + batch], "model_name": model_name, "top_k": top_k},
                )
                response.raise_for_status()
                out.extend(row.get("predictions", []) for row in response.json()["results"])
    except httpx.HTTPStatusError as exc:
        raise PushError(
            f"api_v3 prediction failed (HTTP {exc.response.status_code}){_reason(exc.response)}.", 502,
        ) from exc
    except httpx.HTTPError as exc:
        raise PushError(f"api_v3 prediction failed: {exc.__class__.__name__}.", 502) from exc
    return out
