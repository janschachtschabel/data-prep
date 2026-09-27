"""The per-request LLM override as it arrives over HTTP.

It sits here, beside :mod:`app.routes.field_spec`, rather than in either module it used to
bridge (audit A4). ``app/security.py`` answers key checks, and holding this made every
import of the auth module pull in the LLM client — a keyless instance that never calls an
LLM paid for it too. ``app/llm.py`` is the other direction and worse: it is framework-free,
and a ``Header(...)`` default there would make the LLM client depend on FastAPI.

Header parsing belongs at the boundary, which is this package.
"""

from __future__ import annotations

from fastapi import Header

from ..llm import LlmOverride


def llm_override(
    x_llm_key: str | None = Header(
        default=None, alias="X-LLM-Key",
        description="Optional LLM API key for this request; wins over the server's env key. Kept in "
        "memory only, never stored or logged: a run holds it until it stops, so a resume must send it again.",
    ),
    x_llm_model: str | None = Header(
        default=None, alias="X-LLM-Model",
        description="Optional model for this request, replacing the one config.yaml sets for the "
        "purpose. The endpoint (base_url) always stays the configured one.",
    ),
) -> LlmOverride:
    """Extract per-request LLM credentials from headers (open-instance mode).

    ``X-LLM-Key`` / ``X-LLM-Model`` let a caller drive the LLM with their own
    key + model without any server-wide env key. Both are optional; empty
    strings collapse to ``None`` so the endpoint falls back to config + env.
    The key is a secret — it lives only for the duration of the request (and,
    for a background run, in memory), never persisted or logged.
    """
    return LlmOverride(api_key=x_llm_key or None, model=x_llm_model or None)
