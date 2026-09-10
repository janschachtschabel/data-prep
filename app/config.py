"""Typed access to ``config.yaml``: LLM endpoints per purpose and run budgets.

Kept separate from :mod:`app.settings` (env) on purpose: this file holds
structured, non-secret defaults an operator edits, while secrets stay in env —
the YAML only NAMES the env variable that carries each API key, it never
contains a key itself.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, ValidationError


class LlmEndpoint(BaseModel):
    """One LLM purpose: where to call, which model, and which env var holds the key.

    ``base_url`` is unset by default so the ``provider`` decides it — a
    hardcoded default would always win and make the provider choice inert. Set
    it to point at a private gateway; it then takes precedence.

    ``verbosity`` and ``reasoning_effort`` apply to the gpt-5 and o-series
    families only and are dropped for models that would answer 400.
    """

    provider: Literal["openai", "b-api-openai", "b-api-academiccloud"] = "openai"
    base_url: str | None = None
    model: str
    api_key_env: str = "OPENAI_API_KEY"
    verbosity: Literal["low", "medium", "high"] | None = None
    reasoning_effort: Literal["minimal", "low", "medium", "high"] | None = None


class Budgets(BaseModel):
    """Cost caps. ``max_llm_calls``/``max_tokens_total`` bound ONE run and pause
    it at the cap. ``max_cross_request_*`` add an optional process-wide ceiling
    across all runs and requests — a circuit breaker (0 = disabled)."""

    max_llm_calls: int = 2000
    max_tokens_total: int = 2_000_000
    max_cross_request_calls: int = 0
    max_cross_request_tokens: int = 0


class EmbeddingsConfig(BaseModel):
    """Static embeddings for the semantic gates (leakage + dedupe).

    The default is the project's own compact edu distillation (int8, ~80 MB) —
    similarity thresholding does not need the larger fp32 variants.
    """

    model: str = "JanSchachtschabel/m2v-gte-256-int8-edu"
    leakage_threshold: float = 0.90
    dedupe_threshold: float = 0.95


class Api3Target(BaseModel):
    """Optional push target: the api_v3 instance that trains on the exports.

    ``url`` empty = push disabled. The key again only NAMES an env variable.
    """

    url: str = ""
    api_key_env: str = "DATAPREP_APIV3_KEY"


class DefaultReference(BaseModel):
    """A curated CSV imported into the reference store on startup (PII-scrubbed).

    The path is configured, not hardcoded — relative paths resolve against the
    data-prep folder.
    """

    name: str
    path: str


class ReferencesConfig(BaseModel):
    defaults: list[DefaultReference] = []


class AppConfig(BaseModel):
    """Validated shape of config.yaml."""

    llm: dict[str, LlmEndpoint] = {}
    budgets: Budgets = Budgets()
    # The Bildungs-API gateway both b-api providers route through. Prod by
    # default; point it at staging to test against the other instance.
    b_api_base_url: str = "https://b-api.prod.openeduhub.net"
    embeddings: EmbeddingsConfig = EmbeddingsConfig()
    api_v3: Api3Target = Api3Target()
    references: ReferencesConfig = ReferencesConfig()


def _default_llm() -> dict[str, LlmEndpoint]:
    # gpt-5.6-luna on low verbosity and low reasoning effort for both purposes:
    # the work here is extraction and short generation, where reasoning tokens
    # cost time and money without improving the answer. Everything is
    # overridable in config.yaml, including the provider.
    default = LlmEndpoint(model="gpt-5.6-luna", verbosity="low", reasoning_effort="low")
    return {"seeds": default, "bulk": default.model_copy()}


def load_config(path: Path) -> AppConfig:
    """Parse config.yaml; a missing file yields defaults, a broken one fails loudly."""
    if not path.exists():
        return AppConfig(llm=_default_llm())
    try:
        raw = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        cfg = AppConfig.model_validate(raw)
    except (yaml.YAMLError, ValidationError) as exc:
        raise ValueError(f"Invalid config file {path.name}: {exc}") from exc
    for purpose, endpoint in _default_llm().items():
        cfg.llm.setdefault(purpose, endpoint)
    return cfg
