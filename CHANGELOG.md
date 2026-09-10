# Changelog

All notable changes to data-prep are documented here. Format loosely follows
Keep a Changelog; the project is pre-1.0 and versions track milestones.

## [Unreleased] — the generic table workbench, stage B (2026-09-10)

Design: `docs/plan-2026-09-10-tabellen-werkbank.md`. Continues stage A below.

### Added
- **Duplicates over chosen key columns.** `GET /refine/{name}/duplicates?keys=…`
  reports how many rows share a key and which keys repeat; the `dedupe_keys`
  operation removes them, keeping the `first` or the `last` of each group. The
  existing dedupe filters key on the combined TEXT and answer "is this the same
  material?"; these key on an id or a URL and answer "is this the same record?".
- **Join two datasets** — `POST /refine/{name}/join`, over one or more key
  pairs, in all four join types, with column-collision handling (a suffix, or
  `coalesce` to fill gaps from the right instead of adding a column).
- **`GET /refine/{name}/rows`** — a paginated, searchable viewer. Only the
  requested page is serialised, and both `total` and `matched` are returned so
  "30 of 60" shows a search did what was meant.

### Design decisions worth knowing
- **A join without a target reports its CARDINALITY, not a materialised
  preview.** What an operator needs to know first is how many rows it would
  produce, and a key repeating on both sides multiplies them. Computing that
  from the key counts costs nothing; materialising it is the very thing worth
  avoiding. The join then refuses past a 5-million-row ceiling and says how
  large it would have been.
- **A key is usable only when every part is filled** (`app/refine/keys.py`).
  Keying on (url, source) must not merge two rows that merely share a source and
  both lack a URL. Rows with an incomplete key never match anything, and their
  number is reported.
- **Unmatched rows are counted independently of the join type.** A left join
  silently discards unmatched RIGHT rows; the count makes that loss visible.

### Fixed (found while building, before release)
- **pandas' `merge` matches missing keys with each other**, unlike SQL. Marking
  an incomplete key as `None` therefore joined every keyless row on the left to
  every keyless row on the right — a confident false match. Each now gets a
  row-unique placeholder.
- **A right-only row lost its key.** Dropping the right side's key columns
  before the merge left those rows with no identifying value, so an outer join
  produced rows nothing could identify and a second join on the same key would
  have silently lost them. The key columns now travel through the merge.
- **A composite key with one part missing** was treated as a usable key, which
  would have merged unrelated rows. Caught by the multi-key test.

### Verified on real data
3000 rows of `data_30k.csv` split into two files sharing `ccm:wwwurl`: 477
duplicate groups and 276 rows without a key; the pre-flight predicted
many-to-many with 4430 rows and `explodes: true`; the join then produced 4706
rows (4430 matched + 276 unmatched left). The rows without a URL correctly did
not match each other.

## [Unreleased] — the generic table workbench, stage A (2026-09-10)

Design: `docs/plan-2026-09-10-tabellen-werkbank.md`.

Until now every refine operation was training-data shaped: it required text
columns and a label column. This adds the layer BELOW that — operations that
treat a table as a table, so an export can be worked on before anyone has
decided which column is the label.

### Added
- **Import CSV, JSON or JSONL, plain or gzipped.** `POST /refine/datasets/import`
  gains `format`, `separator`, `encoding` and `list_separator`. `format=auto`
  (the default) reads the extension and detects gzip from the file's magic
  number, so a mislabelled `.csv` that is really gzipped still works.
- **Nested JSON is flattened into dot-path columns.** A WLO edu-sharing export
  wraps every property in a list, so `properties.cclom:title` becomes a plain
  column. Lists of scalars are joined; a list of OBJECTS survives as JSON text
  rather than being silently dropped (a documented limitation).
- **Filter rows by column value** — `POST /refine/{name}/op` with `op: "rules"`.
  Fifteen operators (eq, ne, lt, lte, gt, gte, between, in, not_in, contains,
  starts_with, ends_with, regex, is_empty, not_empty) over any column, joined by
  AND or OR. `starts_with` is what makes a URI stem selectable — the case that
  prompted this, since one `taxonid` field carries two vocabularies and only a
  prefix tells them apart.
- **Keep, drop and rename columns**, through the same endpoint. One WLO record
  expands to well over a hundred columns and few are training material.
- **`GET /refine/{name}/profile`** — per-column fill rate, cardinality, the
  values that dominate, and a numeric range where a column holds numbers.
- **`GET /refine/{name}/download`** — export as CSV, CSV.gz, JSON or JSONL, with
  a choosable separator. Refine datasets previously had no download at all; the
  only way out was a push to api_v3.
- Table operations share the label filters' operation history, so one pipeline
  can mix both kinds of step and still be reconstructable.

### Changed
- **The store now reads and writes all-strings consistently.** `load_dataset`
  read the store back without `keep_default_na=False`, so a cell saved as `""`
  returned as `NaN` and the literal text `"NA"` became a missing value — the
  round trip through the store was not stable. That mattered beyond tidiness:
  the new `is_empty` operator would otherwise have meant different things
  depending on whether a dataset had just been imported or reloaded.
- An empty upload now reports "no data rows" instead of a parse failure.
- `POST /refine/datasets/import` moved to `app/routes/tables.py` (same URL, same
  behaviour). `app/routes/refine.py` was at 332 lines, past the ~300 the
  constitution sets; it is now 304 and the table layer has its own module.

### Measured, not adopted
- An expression language for filters (`jahr >= 2015 and ...`). It needs a parser
  and its own error messages, and `pandas.query` — the obvious shortcut —
  evaluates Python, which would turn a filter box into code execution. The rule
  list covers the same ground and is fully testable.
- A `flatten` toggle on import. The plan listed one; it was cut as speculative,
  since the mitigation for a very wide frame is the column-selection operation
  that now exists.

### Security
- A caller-supplied regular expression is capped at 200 characters. Python's
  `re` has no timeout, so an unbounded pattern can backtrack catastrophically.
- Every rejection in the new operations is a client-safe `ValueError` naming the
  offending column and listing what is available, so a wrong name is a 400 and
  never a 500 with a stack trace.

## [Unreleased] — per-request LLM credentials (open-instance mode, 2026-07-19)

### Added
- **Bring-your-own OpenAI key + model per request.** The AI features (seed
  bootstrap, term refine, generation runs, enrichment) now accept an
  `X-LLM-Key` / `X-LLM-Model` header pair, so the server needs **no** KI key of
  its own — an instance can be left running openly and each user supplies their
  own credentials. The request key wins over the `OPENAI_API_KEY` env fallback;
  absent the headers, behaviour is unchanged (backward compatible).
- UI **"KI-Zugang"** panel (EN/DE) to enter the key + model; stored in the
  browser tab only (sessionStorage), sent as the headers above, and surfaced in
  the Runs model summary. Auto-opens when neither the server nor the user has a
  key yet.
- `GET /config` now reports `key_configured` per LLM purpose and
  `llm_key_from_request_supported`, so the UI knows whether to prompt for a key.

### Security
- The per-request key is a secret: held in memory only — including across a
  **background generation run** (`RunManager`), whose `state.json` records the
  effective *model* but never the key — and never logged. `base_url` is
  deliberately **not** overridable per request (operator-controlled in
  `config.yaml`) so a request cannot redirect the server's outbound call (SSRF).

### Changed
- `OPENAI_API_KEY` is now **optional** in `.env` / `docker-compose.yml` (both the
  standalone and the vServer bundle) — set it only for a shared fallback key.

## [Unreleased] — audit remediation (2026-07-12)

Acting on the whole-codebase audit (`docs/audits/2026-07-12-audit.md`). Batch 1:
the top performance finding, a correctness fix, and documentation drift. Batch 2:
correctness & robustness hardening (path-traversal defense, a resource/accuracy
fix, an API status fix, and a dead-branch cleanup). Batch 3: fail-closed auth
for keyless instances. Batch 4: single-sourced the parity-critical text helpers.
Batch 5: a strict Content-Security-Policy and a UI error-handling fix. Batch 6:
combine input hardening and internationalized a11y strings.

### Security
- **Content-Security-Policy header** (audit quick-win): every non-doc response
  now carries a strict `default-src 'self'; base-uri 'self'; form-action 'self';
  frame-ancestors 'none'; object-src 'none'`. The UI is entirely same-origin
  (no inline scripts/handlers/styles, no external assets), so it cost nothing —
  verified by a live browser smoke-test (all 9 scripts + CSS loaded, JS executed,
  zero CSP violations). Swagger/ReDoc are exempted (they load from a CDN + inline
  init and would otherwise break); a test pins both the policy and the exemption.
- **Keyless auth now fails closed off loopback** (audit T4): with no
  `DATAPREP_AUTH_KEY` configured, auth was disabled for *every* client — a
  network-exposed instance served all data endpoints wide open. Keyless is now a
  loopback-only convenience: a request from a non-loopback peer is refused (403)
  unless a key is set. `/health` stays public; local `127.0.0.1` use is
  unchanged. (Behind a reverse proxy the peer is the proxy, so a key must be
  configured — documented in `.env.example` and the README.)
- **Path-traversal defense in the name-based loaders**: `reference.load_reference`
  and the run worker's `RunManager._load_vocab` built filesystem paths from a
  `name` without `safe_name`. Both are reachable with names that aren't
  guaranteed sanitized at the boundary (the run loads a persisted reference name
  and a vocab run-param), so they now sanitize internally — matching the pattern
  `store.dataset_path` and the seeds route already use. Two unit tests pin the
  rejection.
- **Cross-request LLM spend ceiling** (audit T8): per-run `Budgets` bound one
  request only, so a leaked key or a runaway UI retry loop could drive unbounded
  cost, each call individually "within budget." A process-local `SpendLedger`
  now accumulates calls/tokens across *every* request and background run and
  raises the same `BudgetExceeded` (→ HTTP 429) at a configurable ceiling
  (`budgets.max_cross_request_calls` / `max_cross_request_tokens`). **Disabled by
  default (caps of 0)** — it preserves the shipped unlimited per-run budgets and
  changes nothing until an operator opts in. Single-worker, in-memory (resets on
  restart). Five tests pin the ledger logic, cross-session accumulation, and the
  `session_for` wiring.

### Fixed
- **O(N²) semantic dedupe** (audit T1): both the refine `dedupe_semantic` filter
  and the generation-time `SemanticGate` re-stacked the entire growing vector
  list (`np.vstack(list) @ v`) on every candidate — O(N²) time and allocation
  that thrashed on the ~30k-row datasets the app targets. Replaced with a shared
  append-only `embeddings.VectorIndex` (amortized-O(1) growth; one BLAS matmul
  per check). Behavior-preserved (all semantic-dedupe tests green); a unit test
  pins it against a brute-force max-cosine.
- **`combine` returned 500 on bad `text_columns`** (audit T9): `combine_datasets`
  assumed `text_columns ⊆ target_columns` (else a bare `KeyError`) and non-empty
  (else `text_columns[0]` `IndexError`s when building the dedupe key) — both HTTP
  500. It now validates both up front and raises a client-safe `ValueError` the
  route maps to 400. (Non-empty rather than the audit's suggested `≥2`: a
  single-column dedup is legitimate.)
- **`list_datasets` leaked a file handle and mis-counted rows**: it opened each
  CSV with a bare `path.open(...)` (closed only by GC) and counted physical
  lines — over-counting any dataset whose cells contain newlines (refine stores
  uploads verbatim). Now counts data rows through the CSV parser (single column,
  bounded cost) with no dangling handle. Unit test pins the newline case.
- **`LlmConfigError` now maps to 503, not 502**, on the seed bootstrap/terms
  routes: a missing API key means the request never reached the upstream, so
  bad-gateway was wrong; service-unavailable is correct.
- **Dead branch in the Turtle tokenizer** (`_bareword`): both arms of the
  trailing-dot `if/else` returned the identical value. Collapsed to one return
  (behavior-preserving; Turtle round-trip tests unchanged).
- **Run audit button ignored the HTTP status** (`runs.js`): it used a raw
  `fetch` and rendered `res.text()` regardless, so a 401/404/5xx dumped the raw
  error body into the detail pane. Now goes through `Api.get`, which throws a
  client-safe `ApiError` on non-200 that the existing button handler surfaces —
  consistent with every sibling action.

### Changed
- **Consolidated duplicated text helpers** (audit T7): `split_labels` existed
  twice (`reference.py` with a hardcoded comma + the canonical `textnorm` one)
  and the "combine text columns then clean" logic existed three times
  (`analyze._combined_texts`, an identical `prep._combined`, and an inline copy
  in `combine.py`). Drift between copies would silently break api_v3 row/label
  parity. Now single-sourced: `reference`/`seeds`/`terms` import
  `textnorm.split_labels`; `prep` and `combine` reuse `analyze._combined_texts`.
  Behavior-preserving (full suite unchanged at 227).
- **Split `vocab_formats.py`** (342 lines, two responsibilities): the SkoHub
  Turtle lexer/parser (~260 lines) moved to a new `vocab_turtle.py`, leaving the
  manual concept-list parser. Pure code move — callers import `turtle_to_jsonld`
  from its new home; no logic change (suite unchanged at 229; both files now
  single-responsibility and under the size threshold).
- **Extracted `run_store` from the `RunManager` god-class** (audit T6): the five
  stateless persistence methods (`read_state`, `write_state`, `run_dir`,
  `list_runs`, `reconcile_interrupted`) — which only ever took `settings` and
  touched the runs directory — moved to a new `run_store.py`, leaving
  `RunManager` as pure worker/orchestration. This matches the "run store"
  concept the CLAUDE.md already names. Callers (worker, three route modules,
  startup, one test) updated to the new home; pure move, no logic change (suite
  unchanged at 229, mypy resolves every call site).

### Performance
- **Offloaded heavy refine work off the event loop** (audit T3): the whole-frame
  operations `analyze`, `training_preflight`, `holdout_split` + `balance_report`,
  and `combine_datasets`, plus the `/refine/{name}/filter` dispatch (which covers
  the CPU-heavy `dedupe_semantic`: embedding + an O(N) similarity loop), now run
  via `asyncio.to_thread`, so a large refine no longer freezes `/health`,
  progress polling, or an in-flight generation run. Behavior-preserving (same
  results and the same `ValueError → 400` mapping, which the existing route tests
  exercise through the offload).
- **Thread-safe encoder load** (prerequisite for the filter offload): `Encoder`'s
  lazy `model2vec` load is now guarded by a lock (double-checked), so concurrent
  first `encode()` calls from worker threads load the model exactly once instead
  of racing. Pinned by a deterministic concurrency test. The optional startup
  model pre-warm remains unimplemented — a separate choice, not a correctness gap.

### Frontend
- **Internationalized the remaining hardcoded a11y strings**: three `aria-label`s
  (`Sections`, `Subtree root URI`, `Concept URIs`) and one prose placeholder
  (`one concept URI per line`) bypassed the i18n system, so they stayed English
  for German screen-reader users. Added a `data-i18n-aria` hook to the apply loop
  (mirroring `data-i18n-placeholder`), wired the four elements, and added de/en
  keys. Verified live: all four render in German by default and switch to English
  on toggle. (Technical placeholders — column names, URIs, numeric-range hints —
  are language-neutral and deliberately left as-is.)

### Docs
- Corrected drift the audit flagged: README test count (162 → 222), the numpy
  version rationale (2.4 → 2.x), the `.env.example` path overrides
  (`DATAPREP_DATA_DIR`/`RUNS_DIR`/`CONFIG_FILE`), and the `requirements.txt`
  hash-pin claim (the lock is version-pinned, not hash-pinned).

### Deployment
- **Containerization + CI scaffolding** (audit T2): the app was not deployable
  or gated. Added a CPU-only, torch-free `Dockerfile` (python:3.12-slim,
  non-root, healthcheck, single worker on 8110), `.dockerignore`, and a
  `docker-compose.yml` for local runs with a persistent `/data` volume. Two
  GitHub Actions mirror the api_v3 pattern: `ci.yml` (ruff + mypy + pytest +
  an OpenAPI smoke) and `docker.yml` (a test-gated build & push to GHCR).
  `.gitignore` now excludes every `.env` variant except the template. README +
  CLAUDE.md gained a Deployment section; README test count synced (222 → 235).
  Repo creation, image upload and deployment are left to the operator; the base
  image is a mutable tag to be pinned by digest before a production build.

### Deliberately not changed (audit — won't-fix, by design)
- **T5 · unlimited per-run budget default** — the shipped
  `max_llm_calls`/`max_tokens_total` are effectively unlimited on purpose, so a
  scoped run always finishes rather than pausing mid-way. The cost exposure T5
  raised (no ceiling on a leaked key / retry loop) is now covered by the **T8**
  opt-in process-wide ceiling, so the per-run defaults stay as chosen.
- **T5 · CSV formula-injection neutralization** — prefixing spreadsheet-formula
  cells (`= + - @`) is intentionally NOT applied. `exporter.to_csv` produces the
  exact CSV api_v3 trains on (there is no separate human-only export path), so
  neutralizing cells would corrupt the training text. The spreadsheet-viewer
  risk is accepted in favor of keeping the training data faithful; operators who
  open exports in a spreadsheet should treat them as untrusted input.

This closes the 2026-07-12 audit: every code finding is addressed, and the two
remaining items are recorded here as deliberate design decisions.

## [0.2.0] — 2026-07-11

UX and internationalization round on top of v1 — no breaking changes.

### Added
- **Manual & Turtle vocabularies** (`vocab_formats.py`): create a vocabulary by
  pasting a concept list (`Label`, or `Label | URI` in either order), or upload
  SkoHub Turtle (`.ttl`) — both converge on the same JSON-LD the SKOS parser
  already validates. Dependency-free Turtle reader (tokenizer-based, SKOS subset;
  `rdflib` documented as the upgrade path for arbitrary Turtle). New route
  `POST /vocabs/manual`; `POST /vocabs/import` now also accepts `.ttl`/`.turtle`.
- **Generation context/guidance**: an optional free-text field on a run
  (`RunRequest.guidance`) is woven into the generation prompt and persisted in
  the run params, so it survives resume. Steers vocab-only generation when the
  labels alone are not self-explanatory.
- **"Start here" guide tab**: a landing page walking the three curation
  workflows (AI-only, hybrid, clean-up), shown first by default.
- **Run output explanation**: the Runs tab states exactly what a run produces
  (three fixed text fields + the concept as classification label, mapped to the
  vocabulary's metadata field) with a worked school-subject example, and
  clarifies that Schulfächer/Hochschulfächer share `taxonid` but are different
  value sets.

### Changed
- **Full UI internationalization**: the German/English switch now covers the
  entire interface. Static text (help, form labels, buttons, section headings,
  option lists, placeholders) renders via `innerHTML`/`data-i18n` from a trusted
  in-code dictionary; dynamic runtime strings (errors, statuses, results,
  confirm dialogs, list rows and buttons) go through `I18n.t()` with `{param}`
  interpolation and re-render on language switch, with backend status values
  mapped through `I18n.st()`. Two tests pin that every markup key and every
  `I18n.t()` key resolves in both languages.

### Fixed (feedback round)
- **Tab switch didn't refresh inputs**: a seed set (or vocabulary / reference /
  dataset) created in one tab now shows up in the dependent dropdowns of another
  without a page reload — `switchTab` emits `dataprep-tab-shown` and each module
  refreshes on it, preserving the current selection.
- **Cryptic "Unknown concept"**: an invalid subtree root (e.g. the scheme URL
  instead of a concept URI) now returns an actionable message pointing to a
  concept URI or the "all" mode; the field carries a matching hint.
- **Reference field mapping was invisible**: the reference list now shows which
  columns become title / description / keywords and the label.
- **Cancel was slow to take effect**: the run worker only checked the cancel flag
  at the top of each batch loop, so every worker already waiting on the
  concurrency semaphore fired one more LLM call before noticing — with many
  concepts that drained dozens of extra batches and looked like it never stopped.
  The worker now re-checks the flag right after acquiring the semaphore, so
  waiting workers bail without generating; only the in-flight calls finish. The
  Cancel button shows a "Cancelling…" hint. A red→green test measures it (56
  samples-after-cancel → under 40).
- **A single bad batch failed the whole run** ("Internal error" — usually a
  transient 429 / 5xx / timeout at high concurrency, sometimes an unparsable
  response): the worker now retries each batch (on top of the OpenAI SDK's own
  429/5xx retries, raised 3 → 5) and, when still failing, **skips it and keeps the
  run going** — counted as `failed_batches` and shown in the run details. Only
  terminal problems stop the run: budget (pauses) and a missing API key
  (`LlmConfigError`, fails with a clear message instead of "internal error").
  This handles the *transient* case; parsing was already pydantic
  (`chat.completions.parse` / `model_validate_json`), so the fix was resilience,
  not a different parser. (A *terminal* failure leaving its siblings running — the
  real "reports an error but keeps generating" — is a separate bug, fixed below.)
- **A terminal failure over-generated ~2x the requested rows** (the real cause of
  "reports an error but keeps generating", and of a run finishing far past its
  target — e.g. 71k rows for a 35k target): the generation workers run
  concurrently under `asyncio.gather`, which — when one worker raises a terminal
  error (budget reached, missing key, an unexpected bug) — propagates that error
  but does NOT cancel the sibling workers. They kept running as orphans, still
  appending to `samples.jsonl`; a later resume then started a *second* writer set
  on the same file, so each concept could reach up to 2x its target (the `generated`
  counter desyncs the same way — orphan and resumed sets mutate different in-memory
  state, last write to `state.json` wins). The worker pool now shares a `stop`
  signal: the first failure trips it, the siblings halt after their current
  (already-paid) in-flight batch, and **every worker is awaited before the run
  ends — no orphan survives it**. The first failure still sets the right status
  (budget → paused, else → failed). A red→green test drives it: an orphaned sibling
  drained to the full target; a stopped one halts at its in-flight batch.
- **Runs stuck as a fake "running" after a restart**: a run interrupted by a
  crash/restart was left marked "running" with no worker — the UI could neither
  cancel it (409 "not currently active") nor resume it (status "running" was
  rejected). On startup the run store now reconciles these to a resumable
  **"interrupted"** status (the Runs list shows a Resume button). They never
  blocked new runs (the single-run guard is process-local, not state-file based),
  but they cluttered the list and were a dead end.
- **A corrupted line in `samples.jsonl` killed resume** (the *actual* cause of a
  run stuck failing at ~89% with "Internal error", found in the server traceback):
  the resume rebuild called `json.loads` on every line, so a single partial/blank
  line left by a crash mid-write crashed the whole resume — the run could never
  continue. Resume now skips corrupted/blank lines (logging how many) and rebuilds
  from the rest. Losing one sample among thousands beats a permanently dead run.
- **The same corrupted line also 500'd every export, push and the review browser**
  (found while checking whether the over-generated run was usable for api_v3): the
  exporter's `load_samples` and the review reader each parsed lines with a bare
  `json.loads`, so the leftover partial lines crashed `export.csv` / `export.jsonl`
  / `audit.md` / push and the sample listing — the run's data was fine but
  unreachable. The skip-corrupted rule now lives in one shared reader
  (`samples.read_samples`) used by resume, export and review alike, so a partial
  line is tolerated everywhere, not just on resume. Verified end-to-end: the
  71k-row run exports to the api_v3 CSV schema (source=synthetic, 70 labels).

### Added (feedback round)
- **Two more length profiles**: "kurz" (120–300) and "lang" (800–2000) alongside
  standard and full-text.
- **Seeds clarity**: the "seeds per concept" field explains that a handful of
  examples suffice — seeds are few-shot anchors (capped at 50), while the actual
  dataset size is set later in the run (samples per concept, up to 2000).
- **Generation concurrency**: the default number of concurrent LLM calls per run
  is now **20** (was 8), with a per-run **"Concurrent LLM calls"** control
  (`RunRequest.concurrency`). Effective parallelism is `min(this, number of
  selected concepts)` — batches within one concept stay sequential for the
  anti-repeat list. A test measures the actual peak concurrency to prove it
  engages.
- **Runs page polish**: the context/guidance field ships with an editable German
  default (realistic school-subject catalog entries), and the runs list shows only
  the **10 most recent** (`GET /runs?limit=`, with a "showing 10 of N" note) so it
  stays tidy as runs accumulate.

### Added (term banks)
- **Per-concept term banks** (`terms.py`): the hybrid flow now uses the reference
  data beyond few-shot examples. Building a seed set mines a candidate term list
  per concept — whole keywords from the keyword column plus frequent content
  terms from the text columns (columns selectable — the fields pre-fill with the
  chosen reference's actual column names, editable) — with a document-frequency
  filter that drops common-everywhere noise and a case-insensitive dedupe. The
  mining **scope is controlled separately** from the few-shot seed count: rows per
  label to mine (default 500, meant for 100–1000) and max terms per label (default
  60). `POST /seeds/{name}/terms` lets the
  LLM clean and extend a concept's list for its subject + context. Generation
  injects a rotating subset per batch (`build_generation_prompt(terms=…)`), so the
  synthetic content covers the field's real vocabulary and varies. The seed editor
  shows and refines each concept's bank; the seed list reports term coverage.
  Dependency-free mining (no spaCy) — the LLM does the semantic clean-up/extend.

## [0.1.0] — 2026-07-11

First working version. Builds publishable, fully-synthetic datasets from SkoHub
vocabularies + LLM and refines existing datasets for training in api_v3.

### Generation pipeline
- **Vocabularies** (M2): SKOS/JSON-LD parser (flat + nested), guarded HTTPS
  fetch (allowlist, size cap, no redirects) or file upload, hierarchy/subtree.
- **PII engine** (M3): regex scrub/scan (email, phone, URL, handle), mask-vs-drop,
  aggregate-only reports; conservative phone rule (years/dates/ISBN are safe).
- **References** (M3): optional curated CSVs, PII-scrubbed in memory before storage.
- **LLM layer** (M4): async OpenAI client, GPT-5 compatibility map
  (`max_completion_tokens`, default-temperature-only, strict structured parse),
  `json_object` fallback with one revalidation retry, per-run budget gate.
- **Seeds** (M5): distillation from references + vocab-only bootstrap anchored in
  the concept hierarchy (labels, not URIs); seed editor.
- **Runs** (M6): dry-run cost estimate, async generation worker, length profiles,
  budget-pause + resume, cancel; single active run per process.
- **Gates** (M7): leakage filter (hybrid) + semantic dedupe (model2vec).
- **Export** (M8): api_v3-schema CSV + canonical JSONL + audit report (mandatory
  distribution-shift warning); guarded api_v3 push. Separation guarantee: the
  exporter reads only accepted samples.
- **Review** (M9): sample browser with filters, keyboard approve/discard,
  regenerate discarded via resume.

### Refine toolbox (M10)
- **Analyze + preflight**: distribution/PII overview and a training preflight that
  reproduces api_v3's effective-row count to ±0.
- **Filters**: exact/semantic dedupe, length corridor, label filter, cap-per-label,
  markup cleanup, PII mask/drop — preview vs apply, operation log.
- **Combine**: column-mapping assistant + multi-source merge with provenance and
  conflict resolution (highest-priority source wins).
- **Prepare**: stratified text-disjoint holdout split, balance report, api_v3 push.
- **Label audit**: api_v3 second opinion flags confident gold/prediction
  divergence for editorial review — never auto-relabels.
- **Enrich**: additive LLM keyword/description completion, gap-fill only, marked.

### Platform
- FastAPI app, single-key auth (constant-time), security headers, no-cache UI,
  static no-build admin UI. Python ≥ 3.12.

### Fixed (post-initial, same day)
- **Dark-on-dark selects:** native `<select>` was missing from the color-inherit
  rule and the root lacked a `color-scheme` declaration, so dropdowns rendered
  dark text on the dark theme. Added `color-scheme: light/dark`, put `select` in
  the inherit rule, themed `<option>`, and defined the previously-missing `--ok`
  token (approved-review border).
- **Vocabulary presets:** one-click defaults for the common WLO facets
  (Bildungsstufen, Schulfächer, Hochschulfächer, Zielgruppe) plus clearer help
  text on the Vocabularies tab.
- **Seed explanation:** the Seeds tab now defines what a seed is and walks
  through the vocab-only / hybrid / mixed use cases (accordion; German guide
  updated too).
- **Refine chaining made obvious:** filter/combine/split/enrich now auto-select
  their result so the next operation continues from it, a new
  `GET /refine/{name}/ops` route + UI panel shows the applied-operation history,
  and the panel explains that operations stack (each Apply → a new dataset).
- **Vocabulary metadata field:** vocabularies can now record the dataset column
  their labels belong to (optional on import/fetch, stored, shown in the list);
  the 6 presets pre-fill it (educationalcontext, taxonid, oeh_lrt, …).
- **Default references from config:** `config.yaml → references.defaults` imports
  curated CSVs into the reference store on startup (PII-scrubbed, idempotent, in a
  background thread so startup isn't blocked). Ships with the curated 30k set.
- **Language switcher (first increment):** a German/English toggle over the
  navigation shell (tabs, top bar, login, section headings) via `i18n.js` +
  `data-i18n`, remembered per tab. Form labels and dynamic messages stay English
  for now — the dictionary is built to extend.
