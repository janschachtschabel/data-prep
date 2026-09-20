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
  combine, split into train/holdout, audit labels against a trained model,
  enrich missing fields, and lift under-represented labels to a minimum number
  of rows.

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
.venv/Scripts/python -m pip install -r requirements.txt -c requirements.lock -r requirements-dev.txt   # Windows
# or:  .venv/bin/pip install -r requirements.txt -c requirements.lock -r requirements-dev.txt          # POSIX
```

The `-c requirements.lock` constraint makes a development install resolve to
the same pinned tree CI and the Docker image use; without it the `>=` floors
in `requirements.txt` resolve to whatever is newest that day.

```bash
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

### Running the published image

Instead of building, pull what that workflow published — the package is public,
so no `docker login` is needed:

```bash
docker run -d --name data-prep -p 127.0.0.1:8110:8110 \
  -e DATAPREP_AUTH_KEY=your-key \
  -v data-prep-data:/data \
  ghcr.io/janschachtschabel/data-prep:main
```

The image points `DATAPREP_DATA_DIR` and `DATAPREP_RUNS_DIR` into `/data`
itself, so that one volume keeps datasets, vocabularies, seeds and runs. Add
`-e OPENAI_API_KEY=…` for a server-wide LLM key (without it the instance is
"open": users bring their own key in the UI) and `-e DATAPREP_APIV3_KEY=…` for
the api_v3 push and the label audit.

`config.yaml` — LLM endpoints per purpose, run budgets, api_v3 target — ships
**inside** the image. To change it, mount your own and point the app at it:
`-v /path/to/config.yaml:/app/my-config.yaml -e DATAPREP_CONFIG_FILE=/app/my-config.yaml`.

Tags: `main` (the newest commit on `main`) and `sha-<commit>` for an exact
build; a `vX.Y.Z` release would add version tags. Pin `sha-<commit>` where a
moving tag is not acceptable. The images are built for **linux/amd64** only —
on arm64, build locally (`docker compose up -d --build`) or run under emulation.

## Tests, lint, types

```bash
.venv/Scripts/python -m pytest tests -q                              # the whole suite, offline
.venv/Scripts/python -m ruff check app tests
.venv/Scripts/python -m mypy app --config-file pyproject.toml
.venv/Scripts/python -m pip_audit -r requirements.lock --no-deps     # CVE check, also a CI gate
```

The default test run is offline (LLM/embeddings/api_v3 mocked). One marked live
smoke touches the real LLM: `pytest -m live` (needs `OPENAI_API_KEY`).

## The table workbench

Beside the training-data tools, data-prep works on a table as a table — before
anything has been decided about which column is the label. All of it is in the
**Tables** tab and under `/refine/*`:

| What | Endpoint |
|---|---|
| Import CSV, JSON, JSONL (plain or gzipped), nested JSON flattened to dot-path columns | `POST /refine/datasets/import` |
| Browse rows, paginated and searchable | `GET /refine/{name}/rows` |
| Per-column fill rate, cardinality, common values, numeric range | `GET /refine/{name}/profile` |
| Filter rows by column value; keep/drop/rename columns; remove duplicates by key | `POST /refine/{name}/op` |
| Count duplicates over key columns (non-destructive) | `GET /refine/{name}/duplicates` |
| Join two datasets on one or more keys | `POST /refine/{name}/join` |
| Export as CSV (any separator), CSV.gz, JSON or JSONL — optionally defused for a spreadsheet | `GET /refine/{name}/download` |

Three behaviours are worth knowing before relying on them:

- **Nothing is replaced silently.** Every endpoint that stores something under a
  name — imports, `target` of op/filter/join/split/enrich/combine/balance, references,
  vocabularies, seed sets — answers **409** when the name is taken, and
  replaces it only with `overwrite: true` (a form field for uploads). Naming
  the dataset you are working on as its own target is working in place and
  needs no flag. The UI asks before resending with the flag.
- **Comparisons follow the rule's value, not the column.** The store is
  all-strings, so `"9" > "10"` would be true. A rule comparing against a JSON
  *number* compares numerically; against a *string*, as text. So `jahr >= 2015`
  does arithmetic and `datum >= "2026-01-01"` does the lexicographic comparison
  an ISO date wants. Cells that are not numbers never match a numeric rule, and
  the response reports how many there were.
- **A key is usable only when every part is filled.** Keying on `(url, source)`
  never merges two rows that merely share a source and both lack a URL — for
  duplicates and for joins alike.

A join without a `target` returns its **cardinality**, not a result: how the key
sets relate and how many rows the join would produce, computed without building
it. Past five million rows the join is refused rather than attempted.

## Security model

- Single operator key via `X-API-Key` on every data endpoint; `/health` and the
  static UI are public. The key compares in constant time. With no key
  configured, auth is disabled for **loopback clients only** — a keyless
  instance refuses non-loopback requests (403) rather than serving the network
  wide open. The `.env.example` placeholder `change-me` is refused at
  startup, so a copied-but-unedited file cannot run with a key everyone can
  read in the repository.
- **No rate limiter in the app.** Brute force against `/auth/check` is bounded
  only by the key's entropy: use a long random key and let the reverse proxy
  in front (nginx, caddy) limit request rates. slowapi is a documented v2
  candidate.
- Request bodies are capped at `max_upload_mb` (uploads stream against it,
  JSON bodies are refused by `Content-Length`), gzipped uploads at ten times
  that once inflated. The default is **500 MB compressed**, sized for a full WLO
  export (~130 MB gzipped, ~1.4 GB parsed); the process needs memory to match, and
  an instance reachable by others should lower it.
- No pickle. Secrets only from env, never logged, never in `config.yaml`.
- Uploads are PII-scrubbed on import (references) or handled by an explicit PII
  filter (refine). User-supplied names go through `security.safe_name`.
- **Table workbench limits.** A gzipped upload is refused once it inflates past
  ten times `max_upload_mb`, so a decompression bomb is a 400 rather than an
  out-of-memory. The `regex` rule operator caps a pattern at 200 characters,
  which bounds its size but **not** its backtracking: `(a+)+$` is six
  characters, and Python's `re` has no timeout, so a hostile pattern pins one
  worker thread until it finishes. Accepted because every table endpoint sits
  behind the operator key; do not expose the rule builder to untrusted users.
- **CSV downloads carry every cell as stored — a spreadsheet-safe variant is
  offered beside them.** A cell beginning with `=`, `+`, `-`, `@`, a tab or a
  carriage return is a formula to Excel and LibreOffice (OWASP CSV injection),
  and harvested metadata can hold one: `=HYPERLINK("http://…","click")` in a
  title is one click from an attacker's page. Both downloads therefore take
  `?spreadsheet_safe=true` (UI: the **CSV (Excel)** button in Runs, the checkbox
  in the table export). It puts an apostrophe before such a cell, headers
  included, quotes every field, and offers the file as
  `<name>.spreadsheet.<format>`. It is asked for, never the default: the
  apostrophe is part of the text afterwards, and api_v3 would train on it. The
  plain downloads and both api_v3 pushes stay byte-identical (pinned by tests).
  What the apostrophe covers is the first character of every cell this app
  writes; quoting every field additionally keeps a comma inside a value from
  lying bare in front of a spreadsheet that splits on commas. Two properties of
  that file are worth knowing before handing it on: a number written as text
  (`-5` becomes `'-5`) and no BOM, so Excel still guesses the encoding of the
  umlauts — the plain CSV has always behaved that way, and a BOM for this
  variant alone is an open follow-up.
- **Deliberate deviation from api_v3:** HTTPS URL fetch is allowed for
  vocabularies and the api_v3 push/predict — but only to an allowlist (default
  `vocabs.openeduhub.de` + the configured api_v3 host / localhost), with a size
  cap, a timeout, and only behind auth. `localhost` is accepted over plain
  `http://` for a local api_v3 — a development convenience; a remote api_v3
  must be an allowlisted HTTPS host.
- **Separation guarantee:** a run's exporter reads only accepted synthetic
  samples — reference rows can never reach an export (pinned by test).

## Honest-evaluation discipline

Synthetic and mined rows are **training-only**. Evaluate on a curated,
text-disjoint holdout (the Prepare tool produces one). Internal metrics on mixed
synthetic+curated data overstate real quality — every audit report ships with
this warning.

### Balancing an uneven dataset

Real exports are uneven: a few hundred rows for one subject, three for another.
`POST /refine/{name}/balance` generates the rows each short label is missing —
from that label's own real rows, so the new text resembles the material the label
actually describes. Send `dry_run: true` first. Without opening an LLM session it
answers with, per label, the deficit, what this run would generate, and the share
that would end up synthetic, plus the worst-case number of model calls next to
the call budget. A run that could exceed the call budget is refused before its
first call. A cap no preview can predict — the token cap, the process-wide
ceiling — ends a run early instead of losing it: the rows generated until then
are saved, and the result says where it stopped. `limit` caps one run; running
again on the result continues where it stopped.

Split first, then balance the `_train` part. Balancing first is safe from
leakage too, but the rows shown as examples then leave the holdout, and a rare
label can lose its holdout entirely — the split names such labels
(`labels_without_holdout`).

Three properties make the result safe to train on:

- Every generated row carries `generated_for`, every real row shown to the
  generator as an example carries `example_for`, and every real row enrichment
  completed carries `enriched_fields` (the columns it filled; twins — one text to
  the split, the same labels — are enriched alike). `POST /refine/{name}/split`
  keeps all three on the **training**
  side, with every row sharing their text: a holdout containing generated text —
  or the real text it paraphrases, or a cell the LLM wrote — measures how well a
  model learned the generator. `combine` carries the marks along. A
  `generated_for` value that is not a label of the column being balanced is
  named in the preview (`foreign_marks`) and treated as generated; it does not
  stop the run.
- A label whose real rows carry no text is skipped rather than invented from its
  name or from earlier generated rows, and reported under
  `skipped_without_examples`.
- The result is always a new dataset; a source is never balanced in place.

Read the synthetic share before trusting a number: a label lifted from 3 rows to
100 is a label the model has still barely seen, and its per-label F1 says more
about the generator than about the data. api_v3 reads the same three marks:
marked rows train but never validate, a label no real row can validate gets no
F1, and the model's `synthetic_data` says what its numbers were computed on.
With too few real rows — a pure Runs export has none — it validates on every row
and says so (`validated_on: all_rows`). Judge a model on the holdout as well: it
is the one number from rows no step of the pipeline touched.

## Layout

```
app/            FastAPI app: settings, security, llm, pii, vocab, seeds, runs,
                filtering, exporter, apiv3, refine/ (analyze, filters, combine,
                prep, label_audit, enrich, balance), routes/, static/ui/
docs/           plan (source of truth) + ui-guide.md (German)
config.yaml     LLM endpoints, budgets, embeddings, api_v3 target
```

Design and milestones: [docs/plan-2026-07-10-synthdata-app.md](docs/plan-2026-07-10-synthdata-app.md).
Progress log: [TODO.md](TODO.md).
