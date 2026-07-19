"""Cross-request spend ceiling (audit T8): a process-local ledger that bounds
TOTAL LLM calls/tokens across every request and background run, independent of
the per-run ``Budgets``. Disabled by default (caps of 0), so it never trips
unless an operator opts in.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from pydantic import BaseModel

from app.config import AppConfig, Budgets, LlmEndpoint
from app.llm import BudgetExceeded, LlmSession, SpendLedger, process_ledger


def test_ledger_disabled_never_raises():
    led = SpendLedger()  # caps default to 0 = no ceiling
    for _ in range(1000):
        led.check()
        led.count_call()
        led.count_tokens(10_000)
    assert led.calls == 1000
    assert led.tokens == 10_000_000  # accumulated, but never blocked


def test_ledger_enabled_trips_on_calls():
    led = SpendLedger(max_calls=2)
    for _ in range(2):  # two calls reach the cap
        led.check()
        led.count_call()
    with pytest.raises(BudgetExceeded):
        led.check()  # 3rd call is refused


def test_ledger_enabled_trips_on_tokens():
    led = SpendLedger(max_tokens=100)
    led.check()  # 0 tokens spent — allowed
    led.count_tokens(100)
    with pytest.raises(BudgetExceeded):
        led.check()  # at/over the token cap


class _Schema(BaseModel):
    value: int


def _ok_transport() -> httpx.MockTransport:
    body = {
        "id": "c", "object": "chat.completion", "created": 1, "model": "m",
        "choices": [{"index": 0, "finish_reason": "stop",
                     "message": {"role": "assistant", "content": json.dumps({"value": 1})}}],
        "usage": {"prompt_tokens": 5, "completion_tokens": 9, "total_tokens": 14},
    }
    return httpx.MockTransport(lambda request: httpx.Response(200, json=body))


def test_ledger_accumulates_across_sessions(monkeypatch):
    """Two separate sessions (= two requests) sharing one process ledger: the
    second is refused once the shared cap is reached, proving the ceiling spans
    requests rather than resetting per session."""
    monkeypatch.setenv("TEST_LLM_KEY", "k")
    ledger = SpendLedger(max_calls=1)

    def new_session() -> LlmSession:
        return LlmSession(
            endpoint=LlmEndpoint(model="gpt-5.4-mini", api_key_env="TEST_LLM_KEY"),
            budgets=Budgets(),
            ledger=ledger,
            transport=_ok_transport(),
        )

    result = asyncio.run(new_session().complete("hi", _Schema))  # request 1: allowed
    assert result.value == 1
    with pytest.raises(BudgetExceeded):
        asyncio.run(new_session().complete("hi", _Schema))  # request 2: shared cap hit
    assert ledger.calls == 1  # the refused call was not counted (checked before counting)


def test_session_for_attaches_configured_process_ledger(monkeypatch):
    import types

    from app import llm

    cfg = AppConfig(
        llm={"seeds": LlmEndpoint(model="test")},
        budgets=Budgets(max_cross_request_calls=3, max_cross_request_tokens=99),
    )
    monkeypatch.setattr(llm, "load_config", lambda path: cfg)
    session = llm.session_for("seeds", types.SimpleNamespace(config_file=None))
    assert session.ledger is process_ledger()  # the one process-local ledger
    assert session.ledger.max_calls == 3
    assert session.ledger.max_tokens == 99
