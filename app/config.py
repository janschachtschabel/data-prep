"""Typed access to ``config.yaml``: LLM endpoints per purpose and run budgets.

Kept separate from :mod:`app.settings` (env) on purpose: this file holds
structured, non-secret defaults an operator edits, while secrets stay in env —
the YAML only NAMES the env variable that carries each API key, it never
contains a key itself.
"""

from __future__ import annotations

from pathlib import Path

import yaml
from pydantic import BaseModel, ValidationError


class LlmEndpoint(BaseModel):
    """One LLM purpose: where to call, which model, and which env var holds the key."""

    base_url: str = "https://api.openai.com/v1"
    model: str
    api_key_env: str = "OPENAI_API_KEY"


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
    embeddings: EmbeddingsConfig = EmbeddingsConfig()
    api_v3: Api3Target = Api3Target()
    references: ReferencesConfig = ReferencesConfig()


def _default_llm() -> dict[str, LlmEndpoint]:
    # mini for the quality-critical seed work, nano for bulk generation — both
    # overridable in config.yaml, including base_url for non-OpenAI hosts.
    return {
        "seeds": LlmEndpoint(model="gpt-5.4-mini"),
        "bulk": LlmEndpoint(model="gpt-5.4-nano"),
    }


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
