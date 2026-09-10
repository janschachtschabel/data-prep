"""FastAPI application factory for data-prep."""

from __future__ import annotations

import logging
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request
from fastapi.responses import JSONResponse
from fastapi.staticfiles import StaticFiles

from . import __version__
from .routes import exports, references, refine, review, runs, seeds, system, tables, vocabs
from .settings import get_settings

logging.basicConfig(
    level=get_settings().log_level.upper(),
    format="%(asctime)s %(name)s %(levelname)s %(message)s",
)
logger = logging.getLogger("data_prep")


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Create storage dirs on startup; import configured default references in a
    background thread so a large curated file never blocks readiness."""
    settings = get_settings()
    settings.ensure_dirs()
    if not settings.auth_enabled:
        logger.warning("auth disabled (DATAPREP_AUTH_KEY unset) — local use only")

    # Runs left as 'running' by a crash/restart have no worker — mark them
    # interrupted (resumable) so they are not stuck as a fake 'running'.
    from . import run_store

    interrupted = run_store.reconcile_interrupted(settings)
    if interrupted:
        logger.warning("Marked %d interrupted run(s) from a previous restart as resumable", interrupted)

    import asyncio

    from .config import load_config
    from .reference import seed_default_references

    defaults = load_config(settings.config_file).references.defaults
    if defaults:
        asyncio.create_task(asyncio.to_thread(seed_default_references, settings, defaults))

    logger.info("data-prep ready (data_dir=%s, auth=%s)", settings.data_dir, settings.auth_enabled)
    yield
    logger.info("data-prep shutting down")


_TAGS_METADATA = [
    {"name": "System", "description": "Health check, auth probe and (safe) configuration."},
    {"name": "Vocabularies", "description": "SKOS vocabularies: upload, guarded URL fetch, tree view."},
    {"name": "References", "description": "Optional curated reference sets — PII-scrubbed on import."},
    {"name": "Seeds", "description": "Seed pools per concept: distilled, LLM-bootstrapped, or edited."},
    {"name": "Runs", "description": "Generation runs: dry-run plan, start, progress, cancel, resume."},
    {"name": "Review", "description": "Browse generated samples; approve, discard, regenerate."},
    {"name": "Refine", "description": "Analyze, filter, combine and prepare existing datasets."},
]

_DESCRIPTION = (
    "Dataset workshop for WLO training data: generate publishable, fully "
    "synthetic datasets from SkoHub vocabularies + LLM, and analyze / filter / "
    "combine / enrich existing datasets for training in the classification API.\n\n"
    "**Authentication:** single key via the `X-API-Key` header. `/health` is "
    "public. When no key is configured, auth is disabled for loopback clients "
    "only — requests from a non-loopback address are refused."
)


async def _unhandled_exception_handler(request: Request, exc: Exception) -> JSONResponse:
    """Log the traceback server-side and return a sanitized 500 body so internal
    details (exception text, paths) never leak to clients."""
    logger.exception("Unhandled error on %s %s", request.method, request.url.path)
    return JSONResponse(status_code=500, content={"detail": "Internal server error."})


def create_app() -> FastAPI:
    """Build and configure the FastAPI app."""
    settings = get_settings()
    app = FastAPI(
        title="data-prep",
        description=_DESCRIPTION,
        version=__version__,
        lifespan=lifespan,
        openapi_tags=_TAGS_METADATA,
        swagger_ui_parameters={"persistAuthorization": True},
    )
    app.add_exception_handler(Exception, _unhandled_exception_handler)

    @app.middleware("http")
    async def security_headers(request: Request, call_next):
        """Baseline hardening headers on every response. /ui always revalidates
        (304 when unchanged): browsers otherwise keep executing a stale app.js
        from the heuristic cache after updates — an api_v3 lesson."""
        response = await call_next(request)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["X-Frame-Options"] = "DENY"
        response.headers["Referrer-Policy"] = "no-referrer"
        # The app UI is same-origin only (no inline scripts, no external assets),
        # so a strict CSP costs nothing. Swagger/ReDoc are excluded — they load
        # their bundle from a CDN plus an inline init script and would break.
        if not request.url.path.startswith(("/docs", "/redoc")):
            response.headers["Content-Security-Policy"] = (
                "default-src 'self'; base-uri 'self'; form-action 'self'; "
                "frame-ancestors 'none'; object-src 'none'"
            )
        if request.url.path.startswith("/ui"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    app.include_router(system.router)
    app.include_router(vocabs.router)
    app.include_router(references.router)
    app.include_router(seeds.router)
    app.include_router(runs.router)
    app.include_router(review.router)
    app.include_router(exports.router)
    app.include_router(refine.router)
    app.include_router(tables.router)

    if settings.ui_enabled:
        # Plain static files (no build step, no external assets), same-origin so
        # the browser sends X-API-Key without CORS.
        app.mount("/ui", StaticFiles(directory=Path(__file__).parent / "static" / "ui", html=True),
                  name="ui")
    return app


app = create_app()


if __name__ == "__main__":
    import uvicorn

    # 8110 by default so data-prep can run next to api_v3 (8000) on one machine
    # (8100 turned out to collide with Docker Desktop's backend locally).
    uvicorn.run(app, host="127.0.0.1", port=8110)
