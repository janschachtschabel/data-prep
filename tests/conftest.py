"""Shared fixtures and fakes: isolated app clients, deterministic encoder."""

from __future__ import annotations

from collections.abc import Callable, Iterator

import numpy as np
import pytest
from fastapi.testclient import TestClient


class FakeEncoder:
    """Deterministic, collision-free vectors: known keywords map to fixed
    directions; every other distinct text gets its own registry slot (one-hot),
    i.e. it is orthogonal to everything else."""

    DIM = 4096

    def __init__(self) -> None:
        self._registry: dict[str, int] = {}

    def encode(self, texts: list[str]) -> np.ndarray:
        out = np.zeros((len(texts), self.DIM), dtype=np.float32)
        for i, text in enumerate(texts):
            lowered = text.lower()
            if "brechung" in lowered:
                out[i, 0] = 1.0
            elif "zellaufbau" in lowered:
                out[i, 1] = 1.0
            else:
                slot = self._registry.setdefault(lowered, 2 + len(self._registry))
                assert slot < self.DIM, "FakeEncoder registry exhausted"
                out[i, slot] = 1.0
        return out


@pytest.fixture(autouse=True)
def _reset_spend_ledger() -> Iterator[None]:
    """The cross-request spend ledger is process-local; clear it between tests so
    counters/caps never leak from one case into another."""
    from app.llm import reset_ledger

    reset_ledger()
    yield


@pytest.fixture
def make_client(tmp_path, monkeypatch) -> Iterator[Callable[..., TestClient]]:
    """Factory for a TestClient with temp storage and a configurable auth key.

    ``auth_key=None`` builds the keyless (auth disabled) variant. ``client_addr``
    sets the request peer (loopback by default, matching real local use; pass a
    public address to exercise the non-loopback guard). Extra kwargs become
    ``DATAPREP_<NAME>`` environment variables.
    """
    from app.settings import get_settings

    opened: list[TestClient] = []

    def _make(
        auth_key: str | None = "test-key",
        client_addr: tuple[str, int] = ("127.0.0.1", 50000),
        **overrides: object,
    ) -> TestClient:
        import os

        get_settings.cache_clear()
        monkeypatch.setenv("DATAPREP_DATA_DIR", str(tmp_path / "data"))
        monkeypatch.setenv("DATAPREP_RUNS_DIR", str(tmp_path / "runs"))
        # Isolate from the shipped config.yaml (which seeds the real 30k default
        # reference) unless the test provides its own config.
        if "DATAPREP_CONFIG_FILE" not in os.environ:
            empty_cfg = tmp_path / "empty-config.yaml"
            empty_cfg.write_text("", encoding="utf-8")
            monkeypatch.setenv("DATAPREP_CONFIG_FILE", str(empty_cfg))
        if auth_key is None:
            monkeypatch.delenv("DATAPREP_AUTH_KEY", raising=False)
        else:
            monkeypatch.setenv("DATAPREP_AUTH_KEY", auth_key)
        for name, value in overrides.items():
            monkeypatch.setenv(f"DATAPREP_{name.upper()}", str(value))
        from app.main import create_app

        # Context-managed on purpose: only then does the TestClient keep ONE
        # portal event loop alive across requests, so background tasks
        # (generation runs) actually progress between polls.
        client = TestClient(create_app(), client=client_addr)
        client.__enter__()
        opened.append(client)
        return client

    yield _make
    for client in opened:
        client.__exit__(None, None, None)
    get_settings.cache_clear()
