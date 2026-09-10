"""Which host an LLM purpose talks to, how it authenticates, what it accepts.

Three providers. ``openai`` is OpenAI directly. The two ``b-api-*`` ones go
through OpenEduHub's Bildungs-API gateway, which forwards to OpenAI or to
AcademicCloud/GWDG and is **OpenAI-compatible in its wire format** — so the
existing ``openai.AsyncOpenAI`` client serves all three, and with it the whole
budget, ledger, retry and test-seam machinery.

One difference makes this module necessary: **the gateway authenticates with an
``X-API-KEY`` header, not ``Authorization: Bearer``** (a bearer token gets a
401). The SDK sends the bearer itself, so the extra header is supplied
separately.

The provider is operator-controlled and stays that way — it is read from
``config.yaml``, never from a request header. Same reasoning that keeps
``base_url`` out of per-request overrides: a caller who can redirect the
server's outbound call has an SSRF pivot.

Path scheme and auth measured against b-api staging on 2026-08-21; see the
``wlo-b-api-llm`` skill. Model IDs there change without notice, so a run that
fails with 503 should check ``/models`` before anything else is suspected.
"""

from __future__ import annotations

PROVIDERS = ("openai", "b-api-openai", "b-api-academiccloud")

OPENAI_BASE_URL = "https://api.openai.com/v1"

# The gateway multiplexes upstreams by path segment.
_B_API_UPSTREAM = {
    "b-api-openai": "openai",
    "b-api-academiccloud": "academiccloud",
}

# Model families that take `max_completion_tokens`, refuse a non-default
# temperature, and accept strict structured outputs. Measured: sending
# `max_tokens` to one of these is an HTTP 400.
_TUNABLE_PREFIXES = ("gpt-5", "o1", "o3", "o4")


def _require_provider(provider: str) -> None:
    if provider not in PROVIDERS:
        raise ValueError(
            f"Unknown provider {provider!r}. Available: {', '.join(PROVIDERS)}."
        )


def resolve_base_url(provider: str, explicit: str | None, b_api_base_url: str) -> str:
    """Where this purpose sends its requests.

    An explicit ``base_url`` from the configuration always wins, so an operator
    running a private gateway does not have to fight the provider name.
    """
    _require_provider(provider)
    if explicit:
        return explicit
    upstream = _B_API_UPSTREAM.get(provider)
    if upstream is None:
        return OPENAI_BASE_URL
    return f"{b_api_base_url.rstrip('/')}/api/v1/llm/{upstream}"


def auth_headers(provider: str, key: str) -> dict[str, str]:
    """Extra headers this provider needs beyond what the SDK sends itself.

    Empty for OpenAI (the SDK's bearer token is what it wants) and
    ``X-API-KEY`` for the gateway.
    """
    _require_provider(provider)
    return {"X-API-KEY": key} if provider in _B_API_UPSTREAM else {}


def capabilities(model: str) -> dict:
    """What request body this model accepts.

    The gpt-5 and o-series families take ``max_completion_tokens``, only the
    default temperature, strict schema parsing, and the ``verbosity`` /
    ``reasoning_effort`` controls. Everything else — including every
    vLLM-hosted AcademicCloud model — takes the classic ``max_tokens`` plus
    temperature and a ``json_object`` response format.
    """
    tunable = model.lower().startswith(_TUNABLE_PREFIXES)
    return {
        "token_param": "max_completion_tokens" if tunable else "max_tokens",
        "temperature": not tunable,
        "strict": tunable,
        "verbosity": tunable,
        "reasoning_effort": tunable,
    }
