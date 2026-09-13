"""Application settings: storage paths, auth, UI toggle, logging.

Values come from environment variables (prefix ``DATAPREP_``) and an optional
``.env`` file. Structured, non-secret operator config (LLM endpoints, budgets)
lives separately in ``config.yaml`` — see :mod:`app.config`.
"""

from __future__ import annotations

from functools import lru_cache
from pathlib import Path

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

# The value .env.example ships for the key fields. A copied-but-unedited .env
# must not yield a running instance whose key everyone can read in the repo.
PLACEHOLDER_KEY = "change-me"

# Anchor default paths to the data-prep folder so the app works from any CWD.
_BASE = Path(__file__).resolve().parent.parent


class Settings(BaseSettings):
    """Runtime configuration, loaded from env / .env (prefix ``DATAPREP_``)."""

    model_config = SettingsConfigDict(
        env_prefix="DATAPREP_",
        env_file=".env",
        env_file_encoding="utf-8",
        extra="ignore",
    )

    # --- Storage ---
    data_dir: Path = _BASE / "data"
    runs_dir: Path = _BASE / "runs"
    config_file: Path = _BASE / "config.yaml"

    # --- Admin UI (static single-page app served at /ui; the page itself is
    # public like /docs — every data request from it carries the X-API-Key) ---
    ui_enabled: bool = True

    # --- Auth: single operator key; unset -> auth disabled (local use only) ---
    auth_key: str | None = None

    # --- Limits ---
    # Bounds the COMPRESSED upload, and the exports this app is pointed at are large:
    # the WLO full export is ~130 MB gzipped (~1.4 GB plain), which a 20 MB cap refused
    # with a 413 before the format mattered. Raise the memory the process may use along
    # with it — a gzip this size inflates about tenfold while it parses. A public
    # instance should lower it: DATAPREP_MAX_UPLOAD_MB is the one number to change.
    max_upload_mb: int = 500

    # --- Guarded URL fetch (the ONE deliberate deviation from api_v3's
    # no-URL-fetch rule, see design doc): https-only + host allowlist + cap ---
    fetch_allowed_hosts: str = "vocabs.openeduhub.de"
    fetch_max_mb: int = 5
    fetch_timeout_seconds: int = 20

    # --- Generation: concurrent LLM calls per run (asyncio semaphore). Bounded
    # in practice by the number of selected concepts (batches within one concept
    # are sequential). Per-run overridable via RunRequest.concurrency. ---
    generation_concurrency: int = 20

    # --- Logging ---
    log_level: str = "INFO"

    @field_validator("auth_key")
    @classmethod
    def _refuse_the_placeholder(cls, value: str | None) -> str | None:
        """Fail closed at startup, like the non-loopback 403: a known literal is
        not a secret."""
        if value == PLACEHOLDER_KEY:
            raise ValueError(
                f"DATAPREP_AUTH_KEY is the .env.example placeholder {PLACEHOLDER_KEY!r}: "
                "set a real key, or unset it for loopback-only use."
            )
        return value

    @property
    def fetch_allowed_hosts_list(self) -> list[str]:
        """Parse the comma-separated fetch allowlist into a clean list."""
        return [h.strip().lower() for h in self.fetch_allowed_hosts.split(",") if h.strip()]

    @property
    def auth_enabled(self) -> bool:
        """Auth is on exactly when a key is configured — no separate flag to drift."""
        return bool(self.auth_key)

    def ensure_dirs(self) -> None:
        """Create storage directories if they do not exist."""
        for directory in (self.data_dir, self.runs_dir):
            directory.mkdir(parents=True, exist_ok=True)


@lru_cache
def get_settings() -> Settings:
    """Return the cached singleton settings instance."""
    return Settings()
