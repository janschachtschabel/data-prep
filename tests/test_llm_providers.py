"""Which host a purpose talks to, how it authenticates, and what it accepts.

Three providers. `openai` is OpenAI directly; the two `b-api-*` ones go through
OpenEduHub's Bildungs-API gateway, which is OpenAI-COMPATIBLE in its wire format
but authenticates with an `X-API-KEY` header rather than `Authorization:
Bearer`. That one difference is why this module exists: everything else about
the existing client — budgets, ledger, retries, the test seam — carries over
unchanged.

Measured against the b-api staging instance on 2026-08-21 (see the wlo-b-api-llm
skill): path scheme /api/v1/llm/{academiccloud|openai}/chat/completions.
"""

from __future__ import annotations

import pytest

from app.llm_providers import PROVIDERS, auth_headers, capabilities, resolve_base_url

B_API = "https://b-api.prod.openeduhub.net"


class TestBaseUrl:
    def test_openai_defaults_to_openai(self):
        assert resolve_base_url("openai", None, B_API) == "https://api.openai.com/v1"

    def test_b_api_openai_routes_through_the_gateway(self):
        assert resolve_base_url("b-api-openai", None, B_API) == (
            f"{B_API}/api/v1/llm/openai"
        )

    def test_b_api_academiccloud_routes_to_the_other_provider(self):
        assert resolve_base_url("b-api-academiccloud", None, B_API) == (
            f"{B_API}/api/v1/llm/academiccloud"
        )

    def test_an_explicit_base_url_wins(self):
        """An operator running a private gateway must not have to fight the
        provider name."""
        assert resolve_base_url("b-api-openai", "https://eigen.example/v1", B_API) == (
            "https://eigen.example/v1"
        )

    def test_a_trailing_slash_does_not_produce_a_double_slash(self):
        assert resolve_base_url("b-api-openai", None, B_API + "/") == (
            f"{B_API}/api/v1/llm/openai"
        )

    def test_an_unknown_provider_lists_the_known_ones(self):
        with pytest.raises(ValueError, match="b-api-academiccloud"):
            resolve_base_url("azure", None, B_API)


class TestAuthHeaders:
    def test_the_gateway_wants_x_api_key_not_a_bearer_token(self):
        """Measured: the b-api rejects `Authorization: Bearer` with 401."""
        assert auth_headers("b-api-openai", "geheim") == {"X-API-KEY": "geheim"}
        assert auth_headers("b-api-academiccloud", "geheim") == {"X-API-KEY": "geheim"}

    def test_openai_needs_no_extra_header(self):
        """The SDK sends the bearer token itself; adding one would be noise."""
        assert auth_headers("openai", "geheim") == {}

    def test_an_unknown_provider_is_refused(self):
        with pytest.raises(ValueError, match="azure"):
            auth_headers("azure", "geheim")


class TestCapabilities:
    @pytest.mark.parametrize("model", ["gpt-5.6-luna", "gpt-5.4-mini", "gpt-5-nano"])
    def test_the_gpt5_family_needs_max_completion_tokens(self, model):
        """Measured: sending `max_tokens` to a gpt-5 model is an HTTP 400."""
        caps = capabilities(model)
        assert caps["token_param"] == "max_completion_tokens"
        assert caps["temperature"] is False
        assert caps["strict"] is True

    def test_the_gpt5_family_accepts_verbosity_and_reasoning_effort(self):
        caps = capabilities("gpt-5.6-luna")
        assert caps["verbosity"] is True
        assert caps["reasoning_effort"] is True

    @pytest.mark.parametrize("model", ["qwen3.6-35b-a3b", "deepseek-v4-flash-0731",
                                       "openai-gpt-oss-120b", "mistral-medium-3.5-128b"])
    def test_open_weight_models_keep_the_classic_contract(self, model):
        """The academiccloud models are vLLM-hosted and take the classic body."""
        caps = capabilities(model)
        assert caps["token_param"] == "max_tokens"
        assert caps["temperature"] is True
        assert caps["strict"] is False
        assert caps["verbosity"] is False
        assert caps["reasoning_effort"] is False

    @pytest.mark.parametrize("model", ["o1-preview", "o3-mini", "o4-mini"])
    def test_the_o_series_shares_the_gpt5_contract(self, model):
        """Measured alongside gpt-5: same max_completion_tokens requirement."""
        assert capabilities(model)["token_param"] == "max_completion_tokens"

    def test_every_declared_provider_resolves_and_authenticates(self):
        """PROVIDERS is offered to the UI; an entry either module cannot handle
        would present a choice that always fails."""
        for provider in PROVIDERS:
            assert resolve_base_url(provider, None, B_API).startswith("https://")
            auth_headers(provider, "k")
