"""Settings (env prefix DATAPREP_) and the typed config.yaml loader."""

from __future__ import annotations

import pytest


def test_env_prefix_overrides_defaults(monkeypatch):
    from app.settings import Settings

    monkeypatch.setenv("DATAPREP_LOG_LEVEL", "debug")
    monkeypatch.setenv("DATAPREP_UI_ENABLED", "false")
    s = Settings()
    assert s.log_level == "debug"
    assert s.ui_enabled is False


def test_auth_enabled_follows_the_key(monkeypatch):
    from app.settings import Settings

    monkeypatch.delenv("DATAPREP_AUTH_KEY", raising=False)
    assert Settings().auth_enabled is False
    monkeypatch.setenv("DATAPREP_AUTH_KEY", "k")
    assert Settings().auth_enabled is True


def test_default_paths_anchor_to_the_project_folder():
    from app.settings import _BASE, Settings

    s = Settings(auth_key=None)
    assert s.data_dir == _BASE / "data"
    assert s.runs_dir == _BASE / "runs"
    assert s.config_file == _BASE / "config.yaml"


def test_load_config_missing_file_yields_defaults(tmp_path):
    from app.config import load_config

    cfg = load_config(tmp_path / "does-not-exist.yaml")
    assert cfg.llm["seeds"].model == "gpt-5.4-mini"
    assert cfg.llm["bulk"].model == "gpt-5.4-nano"
    assert cfg.budgets.max_llm_calls == 2000


def test_load_config_reads_and_merges_partial_files(tmp_path):
    from app.config import load_config

    path = tmp_path / "config.yaml"
    path.write_text(
        "llm:\n  bulk:\n    model: my-local-model\n    base_url: https://llm.example.org/v1\n"
        "budgets:\n  max_llm_calls: 5\n",
        encoding="utf-8",
    )
    cfg = load_config(path)
    assert cfg.llm["bulk"].model == "my-local-model"
    assert cfg.llm["bulk"].base_url == "https://llm.example.org/v1"
    # Purposes not mentioned in the file keep their defaults.
    assert cfg.llm["seeds"].model == "gpt-5.4-mini"
    assert cfg.budgets.max_llm_calls == 5
    assert cfg.budgets.max_tokens_total == 2_000_000


def test_load_config_broken_file_fails_loudly(tmp_path):
    from app.config import load_config

    path = tmp_path / "config.yaml"
    path.write_text("llm: [not, a, mapping", encoding="utf-8")
    with pytest.raises(ValueError, match="config"):
        load_config(path)


def test_shipped_config_yaml_parses():
    """The default config.yaml in the repo must always be loadable."""
    from app.config import load_config
    from app.settings import Settings

    cfg = load_config(Settings(auth_key=None).config_file)
    assert set(cfg.llm) >= {"seeds", "bulk"}
