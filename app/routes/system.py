"""System endpoints: health check, auth probe for the UI login, safe configuration."""

from __future__ import annotations

import os

from fastapi import APIRouter, Depends

from .. import __version__
from ..config import load_config
from ..security import require_key
from ..settings import Settings, get_settings

router = APIRouter(tags=["System"])


@router.get("/health", summary="Health check (public)")
async def health() -> dict:
    """Public health check for load balancers and container probes (no auth)."""
    return {"status": "healthy", "version": __version__}


@router.get("/auth/check", summary="Verify an API key")
async def auth_check(
    _: None = Depends(require_key),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Login probe for the UI: 200 when the key is valid (or auth is disabled)."""
    return {"auth": "ok", "auth_enabled": settings.auth_enabled}


@router.get("/config", summary="Safe configuration overview")
async def config(
    _: None = Depends(require_key),
    settings: Settings = Depends(get_settings),
) -> dict:
    """Non-sensitive configuration: LLM endpoints (model, base_url and the key's
    env var NAME — never a key value) plus run budgets.

    Each endpoint also carries ``key_configured`` — whether a server-wide env key
    exists for it — so the UI can decide whether to prompt for a per-request key
    (open-instance mode). ``llm_key_from_request_supported`` advertises that the
    ``X-LLM-Key`` / ``X-LLM-Model`` headers are honoured. Neither leaks a key value.
    """
    cfg = load_config(settings.config_file)
    llm: dict[str, dict] = {}
    for purpose, endpoint in cfg.llm.items():
        info = endpoint.model_dump()
        info["key_configured"] = bool(os.environ.get(endpoint.api_key_env))
        llm[purpose] = info
    return {
        "auth_enabled": settings.auth_enabled,
        "ui_enabled": settings.ui_enabled,
        "llm": llm,
        "llm_key_from_request_supported": True,
        "budgets": cfg.budgets.model_dump(),
    }
