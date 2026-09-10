"""The provider choice, from config.yaml through to the actual HTTP request.

test_llm_providers.py pins the pure functions. This pins that they are USED:
that a purpose configured as `b-api-openai` really sends X-API-KEY to the
gateway path, and that gpt-5.6-luna's verbosity and reasoning_effort really
reach the request body.

No network: httpx.MockTransport is injected into the SDK's client, the same
seam the existing LLM tests use.
"""

from __future__ import annotations

import asyncio
import json

import httpx
import pytest
from pydantic import BaseModel, ValidationError

from app.config import AppConfig, LlmEndpoint, load_config

B_API = "https://b-api.prod.openeduhub.net"


class Answer(BaseModel):
    text: str


def _transport() -> tuple[httpx.MockTransport, list[httpx.Request], list[dict]]:
    """Captures the requests AND their bodies, so headers can be asserted."""
    requests: list[httpx.Request] = []
    bodies: list[dict] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        bodies.append(json.loads(request.content.decode("utf-8")))
        return httpx.Response(200, json={
            "choices": [{"message": {"content": '{"text": "ok"}'}}],
            "usage": {"total_tokens": 5},
        })

    return httpx.MockTransport(handler), requests, bodies


def _run(endpoint: LlmEndpoint, transport, monkeypatch) -> None:
    from app.llm import Budgets, LlmSession

    monkeypatch.setenv("TEST_LLM_KEY", "unit-test-key")
    session = LlmSession(
        endpoint=endpoint, budgets=Budgets(), transport=transport,
    )
    asyncio.run(session.complete("hallo", Answer))


class TestConfiguration:
    def test_the_provider_defaults_to_openai(self):
        assert LlmEndpoint(model="gpt-5.6-luna").provider == "openai"

    def test_base_url_is_unset_by_default_so_the_provider_decides(self):
        """A hardcoded default would always beat the provider and make the
        choice inert."""
        assert LlmEndpoint(model="gpt-5.6-luna").base_url is None

    def test_an_unknown_provider_is_refused_at_load_time(self):
        with pytest.raises(ValidationError):
            LlmEndpoint(model="x", provider="azure")

    def test_the_gateway_base_url_has_a_default(self):
        assert AppConfig().b_api_base_url == B_API

    def test_the_shipped_defaults_use_gpt_5_6_luna_on_low(self, tmp_path):
        cfg = load_config(tmp_path / "absent.yaml")
        for purpose in ("seeds", "bulk"):
            endpoint = cfg.llm[purpose]
            assert endpoint.model == "gpt-5.6-luna"
            assert endpoint.verbosity == "low"
            assert endpoint.reasoning_effort == "low"

    def test_a_yaml_file_can_select_the_gateway(self, tmp_path):
        path = tmp_path / "config.yaml"
        path.write_text(
            "b_api_base_url: https://b-api.staging.openeduhub.net\n"
            "llm:\n"
            "  seeds:\n"
            "    provider: b-api-academiccloud\n"
            "    model: qwen3.6-35b-a3b\n"
            "    api_key_env: B_API_KEY\n",
            encoding="utf-8",
        )
        cfg = load_config(path)
        assert cfg.llm["seeds"].provider == "b-api-academiccloud"
        assert cfg.b_api_base_url == "https://b-api.staging.openeduhub.net"


class TestSessionResolvesTheEndpoint:
    def test_a_gateway_purpose_gets_the_gateway_url(self, tmp_path, monkeypatch):
        from app.llm import session_for
        from app.settings import Settings

        path = tmp_path / "config.yaml"
        path.write_text(
            "llm:\n  seeds:\n    provider: b-api-openai\n    model: gpt-5.6-luna\n",
            encoding="utf-8",
        )
        session = session_for("seeds", Settings(auth_key=None, config_file=path))
        assert session.endpoint.base_url == f"{B_API}/api/v1/llm/openai"

    def test_an_openai_purpose_keeps_the_openai_url(self, tmp_path):
        from app.llm import session_for
        from app.settings import Settings

        path = tmp_path / "config.yaml"
        path.write_text("llm:\n  seeds:\n    model: gpt-5.6-luna\n", encoding="utf-8")
        session = session_for("seeds", Settings(auth_key=None, config_file=path))
        assert session.endpoint.base_url == "https://api.openai.com/v1"


class TestTheRequestOnTheWire:
    def test_the_gateway_gets_x_api_key(self, monkeypatch):
        """Measured: the b-api answers 401 to `Authorization: Bearer` alone."""
        transport, requests, _ = _transport()
        _run(LlmEndpoint(model="gpt-5.6-luna", provider="b-api-openai",
                         base_url=f"{B_API}/api/v1/llm/openai",
                         api_key_env="TEST_LLM_KEY"), transport, monkeypatch)
        assert requests[0].headers["x-api-key"] == "unit-test-key"

    def test_the_gateway_request_goes_to_the_gateway_path(self, monkeypatch):
        transport, requests, _ = _transport()
        _run(LlmEndpoint(model="gpt-5.6-luna", provider="b-api-academiccloud",
                         base_url=f"{B_API}/api/v1/llm/academiccloud",
                         api_key_env="TEST_LLM_KEY"), transport, monkeypatch)
        assert str(requests[0].url) == (
            f"{B_API}/api/v1/llm/academiccloud/chat/completions"
        )

    def test_openai_gets_no_x_api_key(self, monkeypatch):
        transport, requests, _ = _transport()
        _run(LlmEndpoint(model="gpt-5.6-luna", api_key_env="TEST_LLM_KEY"),
             transport, monkeypatch)
        assert "x-api-key" not in requests[0].headers

    def test_verbosity_and_reasoning_effort_reach_the_body(self, monkeypatch):
        transport, _, bodies = _transport()
        _run(LlmEndpoint(model="gpt-5.6-luna", verbosity="low",
                         reasoning_effort="low", api_key_env="TEST_LLM_KEY"),
             transport, monkeypatch)
        assert bodies[0]["verbosity"] == "low"
        assert bodies[0]["reasoning_effort"] == "low"

    def test_they_are_omitted_for_a_model_that_would_reject_them(self, monkeypatch):
        """An AcademicCloud model is vLLM-hosted and takes the classic body;
        sending gpt-5 controls would be a 400."""
        transport, _, bodies = _transport()
        _run(LlmEndpoint(model="qwen3.6-35b-a3b", provider="b-api-academiccloud",
                         base_url=f"{B_API}/api/v1/llm/academiccloud",
                         verbosity="low", reasoning_effort="low",
                         api_key_env="TEST_LLM_KEY"), transport, monkeypatch)
        assert "verbosity" not in bodies[0]
        assert "reasoning_effort" not in bodies[0]
        assert "max_tokens" in bodies[0]

    def test_they_are_omitted_when_not_configured(self, monkeypatch):
        transport, _, bodies = _transport()
        _run(LlmEndpoint(model="gpt-5.6-luna", api_key_env="TEST_LLM_KEY"),
             transport, monkeypatch)
        assert "verbosity" not in bodies[0]


class TestTuningValuesAreCheckedAtLoadTime:
    """provider is a Literal and fails when config.yaml is read; verbosity and
    reasoning_effort were free strings, so a typo surfaced as a 400 on the
    first LLM call instead. Same field, same moment, same behaviour."""

    def test_a_typo_in_verbosity_is_refused_when_the_config_loads(self):
        with pytest.raises(ValidationError):
            LlmEndpoint(model="gpt-5.6-luna", verbosity="lwo")

    def test_a_typo_in_reasoning_effort_is_refused_when_the_config_loads(self):
        with pytest.raises(ValidationError):
            LlmEndpoint(model="gpt-5.6-luna", reasoning_effort="lo")

    def test_the_documented_values_are_accepted(self):
        for value in ("low", "medium", "high"):
            LlmEndpoint(model="gpt-5.6-luna", verbosity=value, reasoning_effort=value)
        LlmEndpoint(model="gpt-5.6-luna", reasoning_effort="minimal")
