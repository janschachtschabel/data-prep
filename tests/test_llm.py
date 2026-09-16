"""LLM layer: capability mapping, structured outputs, budget accounting.

All network I/O is mocked by injecting httpx.MockTransport into the SDK's
http client — no real API calls here. The only real call is the explicitly
marked live smoke at the bottom (excluded by default, run via `pytest -m live`).
"""

from __future__ import annotations

import asyncio
import json
import os

import httpx
import pytest
from pydantic import BaseModel

from app.config import Budgets, LlmEndpoint


class Items(BaseModel):
    items: list[str]


def _chat_response(content: str, prompt_tokens: int = 10, completion_tokens: int = 20) -> dict:
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "created": 1,
        "model": "test-model",
        "choices": [
            {"index": 0, "finish_reason": "stop",
             "message": {"role": "assistant", "content": content}}
        ],
        "usage": {
            "prompt_tokens": prompt_tokens,
            "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens,
        },
    }


def _transport(responses: list) -> tuple[httpx.MockTransport, list[dict]]:
    """Mock LLM host: ints become error status codes, dicts become 200 bodies.
    Captures every request body for parameter assertions."""
    captured: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        captured.append(json.loads(request.content.decode("utf-8")))
        item = responses[min(len(captured) - 1, len(responses) - 1)]
        if isinstance(item, int):
            return httpx.Response(item, json={"error": {"message": "synthetic error"}})
        return httpx.Response(200, json=item)

    return httpx.MockTransport(handler), captured


def _session(model: str, transport: httpx.MockTransport, monkeypatch, **budget_overrides):
    from app.llm import LlmSession

    monkeypatch.setenv("TEST_LLM_KEY", "unit-test-key")
    endpoint = LlmEndpoint(model=model, api_key_env="TEST_LLM_KEY")
    budgets = Budgets(**budget_overrides) if budget_overrides else Budgets()
    return LlmSession(endpoint=endpoint, budgets=budgets, transport=transport)


VALID = _chat_response('{"items": ["Physik", "Chemie"]}')


# ------------------------------------------------------------ capabilities ----


def test_missing_key_env_raises_clear_error(monkeypatch):
    from app.llm import LlmError, LlmSession

    monkeypatch.delenv("SURELY_UNSET_KEY", raising=False)
    endpoint = LlmEndpoint(model="gpt-5.4-nano", api_key_env="SURELY_UNSET_KEY")
    session = LlmSession(endpoint=endpoint, budgets=Budgets())
    with pytest.raises(LlmError, match="SURELY_UNSET_KEY"):
        asyncio.run(session.complete("hi", Items))


def test_gpt5_models_use_max_completion_tokens_and_no_temperature(monkeypatch):
    transport, captured = _transport([VALID])
    session = _session("gpt-5.4-nano", transport, monkeypatch)

    result = asyncio.run(session.complete("zwei Fächer", Items, max_output_tokens=500, temperature=0.8))

    assert result == Items(items=["Physik", "Chemie"])
    body = captured[0]
    assert body["max_completion_tokens"] == 500
    assert "max_tokens" not in body
    assert "temperature" not in body  # gpt-5 family rejects non-default temperature
    assert body["response_format"]["type"] == "json_schema"


def test_other_models_use_max_tokens_and_temperature(monkeypatch):
    transport, captured = _transport([VALID])
    session = _session("mistral-7b-instruct", transport, monkeypatch)

    result = asyncio.run(session.complete("zwei Fächer", Items, max_output_tokens=500, temperature=0.8))

    assert result.items == ["Physik", "Chemie"]
    body = captured[0]
    assert body["max_tokens"] == 500
    assert body["temperature"] == 0.8
    assert body["response_format"] == {"type": "json_object"}


# -------------------------------------------------- validation retry logic ----


def test_invalid_json_retries_once_with_error_hint(monkeypatch):
    transport, captured = _transport([_chat_response("das ist kein JSON"), VALID])
    session = _session("mistral-7b-instruct", transport, monkeypatch)

    result = asyncio.run(session.complete("zwei Fächer", Items))

    assert result.items == ["Physik", "Chemie"]
    assert len(captured) == 2
    assert session.usage.calls == 2
    retry_messages = captured[1]["messages"]
    assert any("JSON" in m["content"] for m in retry_messages if m["role"] == "user")


def test_invalid_json_twice_raises(monkeypatch):
    from app.llm import LlmError

    bad = _chat_response('{"wrong_field": 1}')
    transport, captured = _transport([bad, bad])
    session = _session("mistral-7b-instruct", transport, monkeypatch)

    with pytest.raises(LlmError, match="JSON"):
        asyncio.run(session.complete("zwei Fächer", Items))
    assert len(captured) == 2


# ------------------------------------------------------------------ budget ----


def test_call_budget_blocks_before_the_api_is_hit(monkeypatch):
    from app.llm import BudgetExceeded

    transport, captured = _transport([VALID])
    session = _session("gpt-5.4-nano", transport, monkeypatch, max_llm_calls=1)

    asyncio.run(session.complete("eins", Items))
    with pytest.raises(BudgetExceeded, match="calls"):
        asyncio.run(session.complete("zwei", Items))
    assert len(captured) == 1  # the second request never reached the transport


def test_token_budget_blocks_after_accumulation(monkeypatch):
    from app.llm import BudgetExceeded

    transport, _ = _transport([VALID])  # every response reports 30 tokens
    session = _session("gpt-5.4-nano", transport, monkeypatch, max_tokens_total=25)

    asyncio.run(session.complete("eins", Items))
    assert session.usage.tokens_total == 30
    with pytest.raises(BudgetExceeded, match="token"):
        asyncio.run(session.complete("zwei", Items))


def test_transport_errors_are_retried_by_the_sdk(monkeypatch):
    transport, captured = _transport([429, VALID])
    session = _session("gpt-5.4-nano", transport, monkeypatch)

    result = asyncio.run(session.complete("zwei Fächer", Items))

    assert result.items == ["Physik", "Chemie"]
    assert len(captured) == 2  # SDK retried transparently...
    assert session.usage.calls == 1  # ...but it stays ONE logical (billed) call for us


# ----------------------------------------------------- per-request override ----


def test_request_key_is_used_when_env_is_unset(monkeypatch):
    """The core of the open-instance mode: a key supplied per request (not env)
    reaches the client. With no env var set, the call must still succeed."""
    from app.llm import LlmSession

    monkeypatch.delenv("SURELY_UNSET_KEY", raising=False)
    transport, captured = _transport([VALID])
    endpoint = LlmEndpoint(model="gpt-5.4-nano", api_key_env="SURELY_UNSET_KEY")
    session = LlmSession(endpoint=endpoint, budgets=Budgets(),
                         transport=transport, api_key="byo-request-key")

    result = asyncio.run(session.complete("hi", Items))
    assert result.items == ["Physik", "Chemie"]
    assert len(captured) == 1


def test_request_key_takes_precedence_over_env(monkeypatch):
    """When both exist, the per-request key wins (each caller brings their own)."""
    from app.llm import LlmSession

    monkeypatch.setenv("TEST_LLM_KEY", "env-key")
    transport = httpx.MockTransport(
        lambda req: httpx.Response(200, json=VALID)
    )
    session = LlmSession(endpoint=LlmEndpoint(model="gpt-5.4-nano", api_key_env="TEST_LLM_KEY"),
                         budgets=Budgets(), transport=transport, api_key="request-key")
    # The SDK sends Authorization: Bearer <key>; assert the request-key is used.
    seen: list[str] = []

    def handler(req: httpx.Request) -> httpx.Response:
        seen.append(req.headers.get("authorization", ""))
        return httpx.Response(200, json=VALID)

    session._client = None  # rebuild the client with a capturing transport
    session.transport = httpx.MockTransport(handler)
    asyncio.run(session.complete("hi", Items))
    assert seen == ["Bearer request-key"]


def test_missing_key_error_mentions_both_header_and_env(monkeypatch):
    """The no-key error must name BOTH ways to supply one, so an operator of an
    open instance knows a per-request header is an option."""
    from app.llm import LlmConfigError, LlmSession

    monkeypatch.delenv("SURELY_UNSET_KEY", raising=False)
    session = LlmSession(endpoint=LlmEndpoint(model="gpt-5.4-nano", api_key_env="SURELY_UNSET_KEY"),
                         budgets=Budgets())
    with pytest.raises(LlmConfigError, match="X-LLM-Key"):
        asyncio.run(session.complete("hi", Items))


def test_session_for_applies_override(tmp_path, monkeypatch):
    """session_for merges an override: the request model replaces config's, and
    the request key is threaded onto the session (env untouched)."""
    import yaml

    from app.llm import LlmOverride, session_for
    from app.settings import Settings

    (tmp_path / "config.yaml").write_text(yaml.safe_dump({
        "llm": {"bulk": {"model": "gpt-5.4-nano", "api_key_env": "TEST_LLM_KEY"}},
    }), encoding="utf-8")
    settings = Settings(config_file=tmp_path / "config.yaml")

    session = session_for("bulk", settings,
                          LlmOverride(api_key="req-key", model="gpt-5.4-mini"))
    assert session.endpoint.model == "gpt-5.4-mini"  # request model wins
    assert session.endpoint.base_url == "https://api.openai.com/v1"  # base_url stays config
    assert session.api_key == "req-key"


def test_session_for_without_override_is_unchanged(tmp_path):
    """No override → config model, no request key (env fallback path)."""
    import yaml

    from app.llm import session_for
    from app.settings import Settings

    (tmp_path / "config.yaml").write_text(yaml.safe_dump({
        "llm": {"bulk": {"model": "gpt-5.4-nano"}},
    }), encoding="utf-8")
    settings = Settings(config_file=tmp_path / "config.yaml")

    session = session_for("bulk", settings)
    assert session.endpoint.model == "gpt-5.4-nano"
    assert session.api_key is None


# -------------------------------------------------------------- live smoke ----


@pytest.mark.live
@pytest.mark.skipif(not os.environ.get("OPENAI_API_KEY"), reason="OPENAI_API_KEY not set")
def test_live_smoke_bulk_endpoint_returns_schema():
    """One tiny real call against the default bulk model — proves key handling,
    gpt-5 parameter compatibility and structured parsing against the real API."""
    from app.llm import LlmSession

    session = LlmSession(endpoint=LlmEndpoint(model="gpt-5.4-nano"), budgets=Budgets(max_llm_calls=2))
    result = asyncio.run(
        session.complete(
            'Nenne genau zwei deutsche Schulfächer. Antworte NUR mit JSON: {"items": ["...", "..."]}',
            Items,
            max_output_tokens=500,
        )
    )
    assert len(result.items) == 2
    assert session.usage.calls >= 1 and session.usage.tokens_total > 0


def test_budget_exceeded_is_an_llm_error():
    """Load-bearing, not decoration: five call sites catch LlmError, and a
    budget stop must land in those handlers rather than escaping as a 500.

    Pinned after a refactor nearly flattened the hierarchy to break an import
    cycle -- the cycle was real, the right fix was a shared errors module.
    """
    from app.llm import BudgetExceeded, LlmConfigError, LlmError

    assert issubclass(BudgetExceeded, LlmError)
    assert issubclass(LlmConfigError, LlmError)


def test_the_llm_entry_point_still_exposes_what_callers_import():
    """app.llm is the layer's entry point; routes and runs import these names
    from it. Moving them to their own modules must not change that."""
    import app.llm as llm

    for name in ("BudgetExceeded", "LlmConfigError", "LlmError", "LlmOverride",
                 "LlmSession", "SpendLedger", "Usage", "apply_override",
                 "process_ledger", "session_for"):
        assert hasattr(llm, name), name


# ------------------------------------------------ strict-path failure mapping ----


def test_a_truncated_strict_answer_is_an_llm_error(monkeypatch):
    """The SDK raises LengthFinishReasonError when the output budget ran out. It is an
    OpenAIError but not an APIError, so it escaped every handler as a 500 — and a
    balancing run lost everything it had generated (review #11)."""
    from app.llm import LlmError

    truncated = _chat_response('{"items": ["Phys')
    truncated["choices"][0]["finish_reason"] = "length"
    transport, _ = _transport([truncated])
    session = _session("gpt-5.4-nano", transport, monkeypatch)

    with pytest.raises(LlmError, match="length|truncat"):
        asyncio.run(session.complete("p", Items))


def test_a_strict_answer_breaking_the_schema_is_an_llm_error_without_the_output(monkeypatch):
    """A value over a length bound raised pydantic's ValidationError — a ValueError, so
    the balance route answered 400 with the model's output in the message."""
    from typing import Annotated

    from pydantic import StringConstraints

    from app.llm import LlmError

    class Short(BaseModel):
        items: list[Annotated[str, StringConstraints(max_length=5)]]

    secret = "a value far longer than five characters"
    transport, _ = _transport([_chat_response(json.dumps({"items": [secret]}))])
    session = _session("gpt-5.4-nano", transport, monkeypatch)

    with pytest.raises(LlmError) as caught:
        asyncio.run(session.complete("p", Short))

    assert not isinstance(caught.value, ValueError)
    assert secret not in str(caught.value)


def test_a_missing_endpoint_is_a_configuration_error(tmp_path):
    """Nothing upstream was reached, so callers answer 503 — which they only do for
    LlmConfigError."""
    from app.llm import LlmConfigError, session_for
    from app.settings import Settings

    cfg = tmp_path / "config.yaml"
    cfg.write_text("", encoding="utf-8")

    # seeds and bulk always get a default endpoint; any other purpose has none.
    with pytest.raises(LlmConfigError):
        session_for("nonexistent", Settings(config_file=cfg))


def test_the_call_profile_needs_no_session_and_no_endpoint(tmp_path):
    """The balance preview states a worst case before anything is paid: a strict
    endpoint parses in one call, the JSON-object path may take two, and an unknown
    purpose is counted as two rather than refused."""
    from app.llm import call_profile
    from app.settings import Settings

    budget = tmp_path / "budget.yaml"
    budget.write_text("budgets:\n  max_llm_calls: 77\n", encoding="utf-8")
    json_path = tmp_path / "json.yaml"
    json_path.write_text("llm:\n  bulk:\n    model: gpt-4o-mini\n", encoding="utf-8")

    assert call_profile("bulk", Settings(config_file=budget)) == (1, 77)   # gpt-5 default
    assert call_profile("bulk", Settings(config_file=json_path))[0] == 2
    assert call_profile("nonexistent", Settings(config_file=budget)) == (2, 77)
