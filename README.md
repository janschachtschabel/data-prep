# data-prep — dataset workshop for WLO training data

Companion app to the classification API (api_v3). It **builds publishable,
fully-synthetic datasets** from SkoHub vocabularies + an LLM — vocab-only or
enriched by optional curated references — and **analyzes, filters, combines,
enriches and prepares** existing datasets for training. api_v3 stays untouched
and consumes the exported CSVs.

Two ways in:
- **Generate** a dataset from a vocabulary (+ optional references), review it,
  export or push it to api_v3.
- **Refine** an existing CSV: inspect it, run a training preflight, filter,
  combine, split into train/holdout, audit labels against a trained model, and
  enrich missing fields.

## Requirements

Python ≥ 3.12 (the numpy 2.x type stubs need PEP 695). An OpenAI-compatible API
key is only needed for the LLM features (seeds, generation, enrichment); the
vocabulary, PII, filter, combine, split and preflight tools work without one.

**Two ways to supply the LLM key** — pick either:
- **Server-wide fallback:** set `OPENAI_API_KEY` in `.env` (the classic setup).
- **Open instance (no server key):** leave it unset and let each user enter their
  own OpenAI key + model in the UI's **KI-Zugang** panel. Those travel per request
  as the `X-LLM-Key` / `X-LLM-Model` headers, are kept in the browser tab only, and
  are never persisted or logged on the server — so the instance can be left running
  publicly without holding anyone's secret. A per-request key wins over the env key;
  the request endpoint (`base_url`) stays operator-controlled in `config.yaml`.

## Setup

```bash
cd data-prep
python -m venv .venv
.venv/Scripts/python -m pip install -r requirements.txt -r requirements-dev.txt   # Windows
# or:  .venv/bin/pip install -r requirements.txt -r requirements-dev.txt          # POSIX
cp .env.example .env    # then edit .env
```

Configure secrets in `.env` (auth key, optional `OPENAI_API_KEY`, api_v3 key) and
non-secret operator settings in `config.yaml` (LLM endpoints per purpose, run
budgets, api_v3 target). `config.yaml` only *names* the env variable that holds
each key — it never contains a key itself. `OPENAI_API_KEY` is optional (see the
KI-Zugang / open-instance note above).

## Run

```bash
.venv/Scripts/python -m app.main       # serves http://127.0.0.1:8110
```

Open the admin UI at **http://127.0.0.1:8110/ui** and sign in with
`DATAPREP_AUTH_KEY`. If no key is configured, auth is disabled for loopback
clients only — requests from a non-loopback address are refused (set a key for
remote use).
API docs are at `/docs`. See [docs/ui-guide.md](docs/ui-guide.md) for a
German, non-technical walkthrough.

## Deployment (Docker)

The app ships a CPU-only, torch-free image (non-root, healthcheck, single
worker by design). Local run with the bundled compose file:

```bash
cp .env.example .env          # set DATAPREP_AUTH_KEY + OPENAI_API_KEY
docker compose up -d          # serves http://127.0.0.1:8110/ui
docker compose logs -f
docker compose down           # add -v to also delete the datasets/runs volume
```

Behind a reverse proxy the client peer is the proxy, so a `DATAPREP_AUTH_KEY`
is **required** (keyless mode only serves loopback). Persistent state lives in
the `data-prep-data` volume (`/data`).

`.github/workflows/` holds two GitHub Actions: **ci.yml** (ruff + mypy + pytest
+ OpenAPI smoke on every push/PR) and **docker.yml** (gated build & push to
GHCR on `main` and `vX.Y.Z` tags). Before a production build, pin the base
image by digest (see the note at the top of the `Dockerfile`).

## Tests, lint, types

```bash
.venv/Scripts/python -m pytest tests -q                              # 235 tests
.venv/Scripts/python -m ruff check app tests
.venv/Scripts/python -m mypy app --config-file pyproject.toml
```

The default test run is offline (LLM/embeddings/api_v3 mocked). One marked live
smoke touches the real LLM: `pytest -m live` (needs `OPENAI_API_KEY`).

## Security model

- Single operator key via `X-API-Key` on every data endpoint; `/health` and the
  static UI are public. The key compares in constant time. With no key
  configured, auth is disabled for **loopback clients only** — a keyless
  instance refuses non-loopback requests (403) rather than serving the network
  wide open.
- No pickle. Secrets only from env, never logged, never in `config.yaml`.
- Uploads are PII-scrubbed on import (references) or handled by an explicit PII
  filter (refine). User-supplied names go through `security.safe_name`.
- **Deliberate deviation from api_v3:** HTTPS URL fetch is allowed for
  vocabularies and the api_v3 push/predict — but only to an allowlist (default
  `vocabs.openeduhub.de` + the configured api_v3 host / localhost), with a size
  cap, a timeout, and only behind auth.
- **Separation guarantee:** a run's exporter reads only accepted synthetic
  samples — reference rows can never reach an export (pinned by test).

## Honest-evaluation discipline

Synthetic and mined rows are **training-only**. Evaluate on a curated,
text-disjoint holdout (the Prepare tool produces one). Internal metrics on mixed
synthetic+curated data overstate real quality — every audit report ships with
this warning.

## Layout

```
app/            FastAPI app: settings, security, llm, pii, vocab, seeds, runs,
                filtering, exporter, apiv3, refine/ (analyze, filters, combine,
                prep, label_audit, enrich), routes/, static/ui/
docs/           plan (source of truth) + ui-guide.md (German)
config.yaml     LLM endpoints, budgets, embeddings, api_v3 target
```

Design and milestones: [docs/plan-2026-07-10-synthdata-app.md](docs/plan-2026-07-10-synthdata-app.md).
Progress log: [TODO.md](TODO.md).
