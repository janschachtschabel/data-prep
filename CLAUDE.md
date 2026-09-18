# Project: data-prep (dataset workshop for WLO training data)

Standalone companion app to api_v3: builds **publishable, fully synthetic
datasets** from SkoHub vocabularies + LLM — optionally enriched by curated
reference data — and **analyzes, filters, combines, enriches and prepares**
existing datasets for training in api_v3. api_v3 stays untouched and consumes
the exported CSVs.

## Source of truth
- Design doc (approved Rev. 2): [docs/plan-2026-07-10-synthdata-app.md](docs/plan-2026-07-10-synthdata-app.md)
- Progress: [TODO.md](TODO.md) — milestones M1-M11; update status + log after every work package.

## Tech stack
- Python >= 3.12 (numpy 2.x stubs need PEP 695) · FastAPI + uvicorn · pydantic v2 + pydantic-settings (env prefix `DATAPREP_`, plus `config.yaml`)
- openai (async client + structured outputs; its bundled httpx also serves vocab fetch and api_v3 push)
- model2vec + numpy (CPU embeddings: leakage filter, semantic dedupe)
- pandas (CSV/JSONL, refine operations)
- Deliberately NOT in v1: torch, pickle, spaCy, pyarrow, slowapi (documented v2 candidates).

## Commands (run from data-prep/, project venv `.venv`)
- Setup: `python -m venv .venv` then `.venv\Scripts\python -m pip install -r requirements.txt -r requirements-dev.txt`
- Test: `python -m pytest tests -q`
- Lint: `python -m ruff check app tests`
- Types: `python -m mypy app --config-file pyproject.toml`
  (mypy does NOT auto-discover the config — always pass `--config-file`.)

## Conventions (mirror api_v3 unless stated otherwise)
- Code/comments/docs English; comments explain *why*, not *what*. UI help texts and the user guide are German.
- Test-first for logic: failing test → implement → green. Never weaken a test to pass.
- Files < ~300 lines, split by responsibility. Routes stay thin (auth, validation, HTTP mapping) and delegate to core modules. Known exceptions, tracked in TODO.md: `static/ui/i18n.js` (a dictionary), `static/ui/refine.js`, `tabular.py`.
- No pickle. Secrets only via env; never logged, never committed. Errors never swallowed.
- **LLM key/model may also come per request** (`X-LLM-Key` / `X-LLM-Model` headers → `LlmOverride`) so an instance can run with NO server-wide KI key ("open" mode). The request key wins over `api_key_env`; it is held in memory only (for a background run too) and NEVER persisted to `state.json` or logged. `base_url` stays operator-controlled (no per-request override → no SSRF). Absent the headers, the env key is the fallback (backward compatible).
- User-supplied names go through `security.safe_name`; the API key compares in constant time.
- Single-worker design: run store and background tasks are process-local.
- Refine store files are read and written only through `refine.store.in_store` (one dedicated thread): never on the event loop, and never two steps at once. A write and the check before it go in ONE step (`commit(..., guard=...)`, or `preview_or_apply(..., overwrite=...)`), so a name taken meanwhile is still refused.
- `app/__init__.py` caps BLAS threads to 1 **before** numpy is imported — keep that the first thing the package does.
- **Deliberate deviation from api_v3:** HTTPS URL fetch IS allowed (vocabularies, api_v3 push) — host allowlist (default `vocabs.openeduhub.de` + configured api_v3 target), 5 MB cap, timeout, only behind auth.
- Separation guarantee: `exporter` reads only `runs/<id>/samples.jsonl` (status `passed|approved`) — reference rows can structurally never reach an export; a test pins this.
- Eval discipline: synthetic/mined rows are training-only; honest evaluation happens on curated holdout only.
- Cost control: every LLM stage respects run budgets (`max_llm_calls`, `max_tokens_total`) and supports dry-run. An optional process-wide ceiling (`max_cross_request_*`, default 0 = off) bounds total spend across all requests/runs.

## Deployment
- Docker: `Dockerfile` (python:3.12-slim, non-root, healthcheck, single worker, port 8110) · local: `docker compose up -d` (keys via `.env`). Pin the base image by digest before a production build.
- CI/CD: GitHub Actions in `.github/workflows/` — `ci.yml` (ruff + mypy + pytest + OpenAPI smoke) and `docker.yml` (gated build & push to GHCR). Install target is the version-pinned `requirements.lock` (hash-pinning is a follow-up).

## Coding workflow (better-coding skills)

This project uses the better-coding skill set. Follow this process for all
coding work, and re-invoke the relevant skill before each new package or
coding section — skills unload as the session grows, so do not assume one is
still in context because it loaded earlier.

Pipeline: start (boot, once) → orient (route) → plan (design) → workflow
(implement) → review → verify. Bug: debug → workflow → review → verify.
Whole-repo health/security: audit. UI: pair frontend with workflow.

The skills and what each is for:
- /better-coding-start    — boot the workflow once per session; write/refresh this block
- /better-coding-orient   — route a new/unscoped task; understand unfamiliar code before changing it
- /better-coding-plan     — design & spec a non-trivial feature before code; produce the task list
- /better-coding-workflow — write or change any code (the engineering discipline)
- /better-coding-frontend — build or audit UI: accessibility, UX states, i18n, privacy
- /better-coding-review   — review a diff/PR before merge (read-only, severity-tagged)
- /better-coding-audit    — whole-repo health & security check (12 dimensions)
- /better-coding-debug    — anything broken: bug, failing test, build break (root-cause first)
- /better-coding-verify   — before claiming done/fixed/passing (evidence gate)
- /better-coding-help     — explain the set (for humans)

Re-invocation rule (skills unload — reload before each unit of work):
- Before EACH implementation package or coding section: /better-coding-workflow
  (plus /better-coding-frontend when it touches UI)
- Before reviewing a diff: /better-coding-review
- Before claiming done/fixed/passing: /better-coding-verify
- When something breaks: /better-coding-debug

## Knowledge documents
- [docs/plan-2026-07-10-synthdata-app.md](docs/plan-2026-07-10-synthdata-app.md) — design, architecture, milestones M1-M11, acceptance criteria (source of truth)
- [TODO.md](TODO.md) — running progress state
- [../CLAUDE.md](../CLAUDE.md) — workspace conventions (api_v3) this project mirrors
- api_v3 reference implementations (read-only patterns; never modify):
  - [../api_v3/scripts/generate_synthetic.py](../api_v3/scripts/generate_synthetic.py) — proven LLM recipe: label NAMES not URIs, educational-content framing, mixed material types (video/worksheet/quiz/...), facet rotation, few-shot on real rows, length gate
  - [../api_v3/scripts/enrich_tail.py](../api_v3/scripts/enrich_tail.py) — stratified, text-disjoint holdout split methodology
  - [../api_v3/scripts/eval_holdout.py](../api_v3/scripts/eval_holdout.py) — honest holdout evaluation against api_v3 endpoints
  - [../api_v3/app/static/ui/](../api_v3/app/static/ui/) — UI patterns: slim login bar, keyless probe, pills.js, status polling, help accordions
  - [../api_v3/app/settings.py](../api_v3/app/settings.py) / [../api_v3/app/security.py](../api_v3/app/security.py) — settings + auth patterns
- Example vocabulary (SkoHub JSON-LD): https://vocabs.openeduhub.de/w3id.org/openeduhub/vocabs/educationalContext/index.json
- Empirical lessons already baked into the plan (api_v3 enrichment experiments, 2026-07):
  - Internal metrics of mixed synthetic+curated training are misleading (looked +0.07, was −0.075 on curated holdout) — never evaluate on synthetic rows.
  - The quality ceiling is label consistency, not volume: ~300 examples/label is the knee; beyond ~800-1000 no further gain.
