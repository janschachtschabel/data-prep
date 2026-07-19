"""Guarded push of a CSV to the configured api_v3 instance — shared by the run
exporter and the refine toolbox so both use the SAME guards.

Guard: the target host must be on the fetch allowlist or localhost (dev). The
API key comes from the env variable NAMED in config.yaml (never a literal key).
"""

from __future__ import annotations

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


async def push_csv(settings: Settings, csv_text: str, filename: str) -> dict:
    """Upload ``csv_text`` to ``<api_v3.url>/datasets/import``; returns api_v3's
    JSON. Raises :class:`PushError` (400 guard/config, 502 upstream)."""
    import os

    target = load_config(settings.config_file).api_v3
    if not target.url:
        raise PushError("api_v3 push is not configured (config.yaml: api_v3.url).")
    host = (urlparse(target.url).hostname or "").lower()
    if host not in settings.fetch_allowed_hosts_list and host not in _LOCAL_HOSTS:
        raise PushError(f"Push host {host!r} is not allowed.")
    key = os.environ.get(target.api_key_env)
    if not key:
        raise PushError(f"Environment variable {target.api_key_env!r} is not set for the api_v3 key.")

    try:
        async with httpx.AsyncClient(
            transport=_test_transport, timeout=settings.fetch_timeout_seconds  # type: ignore[arg-type]
        ) as client:
            response = await client.post(
                target.url.rstrip("/") + "/datasets/import",
                headers={"X-API-Key": key},
                files={"file": (filename, csv_text.encode("utf-8"), "text/csv")},
            )
            response.raise_for_status()
            return response.json()
    except httpx.HTTPStatusError as exc:
        raise PushError(f"api_v3 rejected the upload (HTTP {exc.response.status_code}).", 502) from exc
    except httpx.HTTPError as exc:
        raise PushError(f"api_v3 push failed: {exc.__class__.__name__}.", 502) from exc


async def predict_batch(
    settings: Settings, texts: list[str], model_name: str, *, top_k: int = 3, batch: int = 500
) -> list[list[dict]]:
    """Ranked predictions per text from the configured api_v3 model — used by the
    label audit for a second opinion. Same guards as :func:`push_csv`."""
    import os

    target = load_config(settings.config_file).api_v3
    if not target.url:
        raise PushError("api_v3 is not configured (config.yaml: api_v3.url).")
    host = (urlparse(target.url).hostname or "").lower()
    if host not in settings.fetch_allowed_hosts_list and host not in _LOCAL_HOSTS:
        raise PushError(f"api_v3 host {host!r} is not allowed.")
    key = os.environ.get(target.api_key_env)
    if not key:
        raise PushError(f"Environment variable {target.api_key_env!r} is not set for the api_v3 key.")

    out: list[list[dict]] = []
    try:
        async with httpx.AsyncClient(
            transport=_test_transport, timeout=settings.fetch_timeout_seconds  # type: ignore[arg-type]
        ) as client:
            for start in range(0, len(texts), batch):
                response = await client.post(
                    target.url.rstrip("/") + "/predict/batch",
                    headers={"X-API-Key": key, "Content-Type": "application/json"},
                    json={"texts": texts[start:start + batch], "model_name": model_name, "top_k": top_k},
                )
                response.raise_for_status()
                out.extend(row.get("predictions", []) for row in response.json()["results"])
    except httpx.HTTPStatusError as exc:
        raise PushError(f"api_v3 prediction failed (HTTP {exc.response.status_code}).", 502) from exc
    except httpx.HTTPError as exc:
        raise PushError(f"api_v3 prediction failed: {exc.__class__.__name__}.", 502) from exc
    return out
