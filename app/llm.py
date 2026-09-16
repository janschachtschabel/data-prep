"""LLM access layer — the ONLY module that talks to a language model.

Purposes (seeds/bulk) come from config.yaml as :class:`LlmEndpoint` entries;
secrets stay in env (the endpoint only NAMES the variable). A capability map
keeps gpt-5-family calls compatible (``max_completion_tokens``, default
temperature only, strict structured outputs) while other OpenAI-compatible
hosts get the classic ``max_tokens`` + ``json_object`` contract. Every logical
call is budgeted — crossing a cap raises :class:`BudgetExceeded` so a run
pauses instead of silently continuing to spend.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from typing import Any, TypeVar

import httpx
import openai
from pydantic import BaseModel, ValidationError

from .config import Budgets, LlmEndpoint, load_config
from .llm_budget import SpendLedger, Usage, process_ledger
from .llm_errors import BudgetExceeded, LlmConfigError, LlmError
from .llm_providers import auth_headers, capabilities, resolve_base_url
from .settings import Settings

logger = logging.getLogger("data_prep.llm")

T = TypeVar("T", bound=BaseModel)



@dataclass(frozen=True)
class LlmOverride:
    """Per-request LLM credentials — the open-instance mode.

    Lets a caller supply their OWN API key and model with the request (via the
    ``X-LLM-Key`` / ``X-LLM-Model`` headers) instead of relying on a server-wide
    env key, so the instance can run without any KI secret configured. The key
    is a secret: hold it in memory only, never persist or log it. ``base_url``
    is deliberately NOT overridable — a request must not be able to redirect the
    server's outbound call to an arbitrary host (SSRF); the endpoint stays
    operator-controlled in config.yaml.
    """

    api_key: str | None = None
    model: str | None = None

    def is_empty(self) -> bool:
        return not (self.api_key or self.model)



def apply_override(endpoint: LlmEndpoint, override: LlmOverride | None) -> tuple[LlmEndpoint, str | None]:
    """Merge a per-request override onto a configured endpoint.

    Returns the (possibly model-swapped) endpoint plus the request API key (or
    ``None`` to fall back to the env var). ``base_url`` and ``api_key_env`` are
    never touched — the request may pick a model and bring a key, nothing else.
    """
    if override is None or override.is_empty():
        return endpoint, None
    merged = endpoint.model_copy(update={"model": override.model}) if override.model else endpoint
    return merged, override.api_key


def session_for(
    purpose: str, settings: Settings, override: LlmOverride | None = None
) -> LlmSession:
    """Budgeted session for a configured purpose (``seeds`` / ``bulk``).

    An optional :class:`LlmOverride` supplies a per-request model and API key
    (open-instance mode); absent that, the configured model + env key are used.
    """
    cfg = load_config(settings.config_file)
    endpoint = cfg.llm.get(purpose)
    if endpoint is None:
        raise LlmError(f"No LLM endpoint configured for purpose {purpose!r} in config.yaml.")
    endpoint, api_key = apply_override(endpoint, override)
    endpoint = endpoint.model_copy(update={"base_url": resolve_base_url(
        endpoint.provider, endpoint.base_url, cfg.b_api_base_url)})
    return LlmSession(
        endpoint=endpoint, budgets=cfg.budgets, ledger=process_ledger(cfg.budgets), api_key=api_key
    )


def _summary(exc: ValidationError) -> str:
    """Where and why the output broke the schema — never the offending value itself."""
    return "; ".join(
        f"{'.'.join(str(p) for p in e['loc'])}: {e['msg']}" for e in exc.errors()[:3]
    )


@dataclass
class LlmSession:
    """One budgeted channel to one endpoint — typically one session per run."""

    endpoint: LlmEndpoint
    budgets: Budgets
    usage: Usage = field(default_factory=Usage)
    ledger: SpendLedger | None = None  # process-wide cross-request ceiling (opt-in)
    # Per-request API key (open-instance mode). Takes precedence over the env
    # var; a secret, so never logged or persisted. repr=False keeps it out of
    # dataclass reprs / tracebacks.
    api_key: str | None = field(default=None, repr=False)
    transport: httpx.AsyncBaseTransport | None = None  # test seam (httpx.MockTransport)
    _client: openai.AsyncOpenAI | None = field(default=None, repr=False)

    async def complete(
        self,
        prompt: str,
        schema: type[T],
        *,
        max_output_tokens: int = 2000,
        temperature: float | None = None,
    ) -> T:
        """One structured completion, validated into ``schema``.

        Raises :class:`BudgetExceeded` when a cap is hit and :class:`LlmError`
        for auth/transport/validation failures.
        """
        caps = capabilities(self.endpoint.model)
        params: dict[str, Any] = {caps["token_param"]: max_output_tokens}
        if temperature is not None:
            if caps["temperature"]:
                params["temperature"] = temperature
            else:
                logger.debug(
                    "Dropping temperature=%s — %s only supports the default.",
                    temperature, self.endpoint.model,
                )
        # Only for the families that accept them; anything else answers 400.
        for control in ("verbosity", "reasoning_effort"):
            value = getattr(self.endpoint, control)
            if value and caps[control]:
                params[control] = value
        client = self._get_client()
        if caps["strict"]:
            return await self._complete_strict(client, prompt, schema, params)
        return await self._complete_json_object(client, prompt, schema, params)

    # ------------------------------------------------------------ internals --

    def _get_client(self) -> openai.AsyncOpenAI:
        if self._client is None:
            # Per-request key (open-instance mode) wins; else the server-wide env
            # fallback. Either path keeps the secret out of config files.
            key = self.api_key or os.environ.get(self.endpoint.api_key_env)
            if not key:
                raise LlmConfigError(
                    "No LLM API key available: send it per request via the X-LLM-Key "
                    f"header, or set the {self.endpoint.api_key_env!r} environment variable."
                )
            http_client = httpx.AsyncClient(transport=self.transport) if self.transport else None
            self._client = openai.AsyncOpenAI(
                api_key=key,
                # The gateway wants X-API-KEY; the SDK's own bearer token is
                # harmless beside it and goes to the same host.
                default_headers=auth_headers(self.endpoint.provider, key),
                base_url=self.endpoint.base_url,
                max_retries=5,  # SDK handles 429/5xx backoff; counts as ONE logical call
                timeout=120.0,
                http_client=http_client,
            )
        return self._client

    def _check_budget(self) -> None:
        if self.usage.calls + 1 > self.budgets.max_llm_calls:
            raise BudgetExceeded(
                f"Budget reached: {self.usage.calls} LLM calls (cap {self.budgets.max_llm_calls})."
            )
        if self.usage.tokens_total >= self.budgets.max_tokens_total:
            raise BudgetExceeded(
                f"Budget reached: {self.usage.tokens_total} tokens (cap {self.budgets.max_tokens_total})."
            )

    def _reserve_call(self) -> None:
        """Budget-check AND count the call synchronously BEFORE awaiting the
        transport — concurrent workers on one session would otherwise both pass
        the check between each other's check and record (asyncio interleaving).
        Both caps are checked before either counter moves, so a refused call is
        never counted."""
        self._check_budget()
        if self.ledger is not None:
            self.ledger.check()
        self.usage.calls += 1
        if self.ledger is not None:
            self.ledger.count_call()

    def _record_tokens(self, response: Any) -> None:
        if response.usage is not None:
            spent = response.usage.total_tokens or 0
            self.usage.tokens_total += spent
            if self.ledger is not None:
                self.ledger.count_tokens(spent)

    async def _complete_strict(
        self, client: openai.AsyncOpenAI, prompt: str, schema: type[T], params: dict[str, Any]
    ) -> T:
        self._reserve_call()
        try:
            response = await client.chat.completions.parse(
                model=self.endpoint.model,
                messages=[{"role": "user", "content": prompt}],
                response_format=schema,
                **params,
            )
        except openai.LengthFinishReasonError as exc:
            # Out of output tokens. Not an APIError, so it used to escape every
            # handler as a 500 and take a whole paid run with it.
            raise LlmError(
                "Model answer was cut off at the output limit (finish_reason=length)."
            ) from exc
        except openai.OpenAIError as exc:  # APIError and the other finish-reason errors
            raise LlmError(f"LLM call failed: {exc.__class__.__name__}.") from exc
        except ValidationError as exc:
            # A ValueError to anyone catching broadly, and its message quotes the
            # model's output; neither belongs in an HTTP answer.
            raise LlmError(f"Model output did not match the schema: {_summary(exc)}") from exc
        self._record_tokens(response)
        parsed = response.choices[0].message.parsed
        if parsed is None:
            raise LlmError("Model returned no parsable content (refusal or empty response).")
        return parsed

    async def _complete_json_object(
        self, client: openai.AsyncOpenAI, prompt: str, schema: type[T], params: dict[str, Any]
    ) -> T:
        messages: list[Any] = [{"role": "user", "content": prompt}]
        last_error = ""
        for attempt in (1, 2):  # one revalidation retry with the error fed back
            self._reserve_call()
            try:
                response = await client.chat.completions.create(
                    model=self.endpoint.model,
                    messages=messages,
                    response_format={"type": "json_object"},
                    **params,
                )
            except openai.APIError as exc:
                raise LlmError(f"LLM call failed: {exc.__class__.__name__}.") from exc
            self._record_tokens(response)
            content = response.choices[0].message.content or ""
            try:
                return schema.model_validate_json(content)
            except ValidationError as exc:
                last_error = _summary(exc)
                if attempt == 1:
                    messages = [
                        *messages,
                        {"role": "assistant", "content": content},
                        {
                            "role": "user",
                            "content": (
                                f"That was not valid JSON for the required schema ({last_error}). "
                                "Return ONLY valid JSON, nothing else."
                            ),
                        },
                    ]
        raise LlmError(f"Model returned invalid JSON twice; last error: {last_error}")
