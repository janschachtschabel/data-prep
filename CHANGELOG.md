# Changelog

All notable changes to data-prep are documented here. Format loosely follows
Keep a Changelog; the project is pre-1.0 and versions track milestones.

## [Unreleased] — AI provenance end to end, prompts that know the dataset (2026-09-19)

Plan: `docs/plan-2026-09-19-ai-provenance-prompts.md`; api_v3 reads the same marks
(its plan `docs/plans/2026-09-19-ai-marked-rows.md`).

### Changed

- **The split keeps every AI-marked row out of the holdout**, `enriched_fields` rows
  (and their text group) included: a real row whose keywords or description an LLM
  wrote measured the model on LLM text. The result line and the no-holdout advice
  name enrichment too.
- **Runs exports mark every row `generated_for=<concept>`.** `source=synthetic` alone
  did not survive a combine, which overwrites `source`. Exports are rebuilt from the
  run on every download or push, so re-exporting marks older runs; a CSV downloaded
  before this version carries no mark.
- **The balancing prompt knows the dataset:** the label by its display name
  (`<label>_DISPLAYNAME`) instead of a URI; the other labels to keep apart from
  (co-occurring first, then by support, at most 30); per field its kind and the
  middle half of its length in the label's real rows (the dataset's, when the label
  has too few). Examples are complete rows closest to the label's median length —
  longest-first made generated rows longer than the real ones. The output budget
  covers the typical length asked for.
- **The enrichment prompt names the row's labels** and asks for text that fits them,
  and states the field's typical length measured on rows people wrote.
- **A guidance that states a length keeps it** ("100-400 Zeichen", "3-6
  Schlagwörter"): neither prompt adds the dataset's range beside it, so a request in the
  old `mode` shape keeps its instructions. A number that is no length ("Klasse 5") does
  not count. The UI's default guidance for the WLO fields names no number any more.
- **A stated typical length stays well below what an answer may hold:** at most 1,500 of
  the 2,000 characters a value may hold, since a model aims at the upper end and
  overshoots it. A field whose typical entry starts beyond that is stated as about 1,500
  characters, one typically longer than an answer may hold gets no length at all, and a
  list whose typical cell is longer gets its capped characters instead of a count.
- **What enrichment wrote is not the dataset:** balancing measures lengths without the
  cells an earlier enrichment filled and shows rows carrying them as examples only when
  no untouched row is left, whether complete or not. Examples sit at the median of the
  label's complete untouched rows, or of its untouched rows when none is complete.
- **Enrichment of a list field** states the typical count of the whole cell
  ("insgesamt"), not of the values to add.
- **Every name from the dataset is bounded** (one line, 60 characters) and the contrast
  list never names the label itself or a name twice.
- **The JSONL export carries `generated_for`** too.

### Fixed (review of 2026-09-19)

- **One answer value too long no longer costs the run.** The 2,000-character cap moved
  from the answer schemas, where one such value failed the whole answer, the route
  answered 502 and every row the run had paid for was lost, to where the answer is read:
  balancing discards that item (`discarded_long`, named in the result line), enrichment
  drops that value; a single-valued field takes the first value only. The enrichment
  schema's cap of 20 values per answer, below the counts a prompt may state, went the
  same way: the list is cut after 50 where it is read.
- **A batch asks for no more entries than its answer has room for.** Up to 50 long
  entries were cut off at the 16,000-token output cap. The preview now counts the calls
  of the batches that fit, and the call budget is checked against those.
- **Twins are enriched alike** — rows that read as one text to the split (their fields,
  cleaned and joined) and carry the same labels — past `limit` and after a stop too,
  without a call of their own; each merges the answer with the values it holds. Enriched
  apart, the untouched twin kept the text its enriched twin trains on and could land in
  the holdout, or in api_v3's validation, beside it.
- **Display names by majority:** one mis-paired row, or a name cell `label_filter` left
  behind, no longer names a label wrongly in every prompt. `label_filter` keeps
  `<label>_DISPLAYNAME` in step with the labels it keeps (clearing names it cannot pair)
  and reports the rows it rewrote as `changed`.
- **A label without a display name keeps the end of its value** (`…/sekundarstufe_1`):
  cut after 60 characters, two URIs of one vocabulary read the same, and the sibling
  fell out of the contrast list. A double quote in a name no longer ends the prompt's
  quotation of it.
- **A stated length is recognised in inflected forms and number words** ("in 2-3
  Sätzen", "zwei bis drei Sätze", "in einem Satz" — "ein" counts only sentences, words
  and characters), and a grade or an age before a unit ("für Klasse 5/6 geeignete
  Begriffe", "ab 6 Jahren") is no length.
- **The enrichment prompt asks for text that fits the classification without naming
  it:** one of six enriched rows had got "Sekundarstufe" as a keyword.
- **The balancing prompt offers its examples for tone and style**, not length, and says
  long fields are cut.
- **The holdout share is a share of the rows that may go there:** counted over every
  real row, a label mostly completed or shown as examples could lose all its free rows
  to the holdout.
- **A target column `enriched_fields` cannot name** — a comma, surrounding spaces — is
  refused with 400, and a mark already in the cell is not added twice.
- **The audit note of a Runs export** says when api_v3 validates on every row after all.
- **The README** names all three marks and says api_v3 reads them.

### Security

- **anyio 4.14.1 → 4.14.2** in `requirements.lock` (CVE-2026-63374, CVE-2026-64847,
  CVE-2026-63349). pip-audit in CI flagged the pin; a patch release of the dependency
  starlette and httpx share, with the same requirements.

### Internal

- `app/refine/prompt_context.py` holds what both prompts read from the frame.

## [Unreleased] — follow-ups of the second review (2026-09-18)

### Fixed

- **A cap that stops an enrichment run keeps what it enriched**, as balancing
  already did: the rows filled until then are saved, the result and the
  operation history say why the run ended, and once the budget allows another
  run on the result fills the remaining gaps. A run stopped before its first
  change still answers 429 and writes nothing. The stop note of both runs no
  longer promises an immediate continuation: after the process-wide ceiling
  (`Cross-request budget reached`) that takes a restart or a higher ceiling.
- **Loading or saving a table no longer stalls the server.** Every refine route
  read and wrote its datasets on the event loop; with the 81 MB WLO export,
  `/health` and run polling waited up to 2.4 s during an import, 1.0 s during a
  load and 0.8 s while a step was applied (now 0.20 / 0.05 / 0.07 s). The store's
  files now go through one dedicated thread (`refine.store.in_store`). One thread,
  not the shared pool: the check before a write and the write itself stay one
  step (`commit`), so a name another request took meanwhile is still refused —
  with several threads both writers passed the check, which on Windows failed
  the second rename and on Linux would have replaced the first table unasked.
  Pushing to api_v3 serialises the table off the loop too.
- **A result's history belongs to the table it was made from.** Every step that
  records provenance -- split, enrichment, balancing, filters, table operations,
  joins -- reads the source table and its history in one store step. Read apart,
  a write in between put a newer version's steps into the result's provenance.
  Filters, table operations and joins read the history only when writing and
  balancing only after planning, both before the move off the loop; split and
  enrichment got the gap with that move. `/ops` checks and reads in one step too: it answers 404 or the real
  history, never an empty history for a table deleted meanwhile.
- **A preview never waits for the store.** A filter or table-operation preview
  writes nothing, so it no longer queues behind other requests' saves on the
  store's thread.

## [Unreleased] — second review of the balancing work (2026-09-18)

A fresh review of the first round's fixes found 2 major, 6 minor and 5 small
issues, all reproduced; two were introduced by that round. Decisions D2, D5 and
D6 are revised in the plan.

### Fixed

- **The length check discarded what the prompt asked for.** It judged answers
  against full example cells while the model saw 400 characters of them; on the
  WLO export about 200 of 500 planned rows would have been paid for and thrown
  away. Prompt, length floor, output budget and duplicate check now all use the
  examples as shown, and the floor only catches fragments.
- **Enrichment's `limit` no longer capped calls** after the first round; it does
  again.
- **A cap that stops a balancing run keeps what was paid for** instead of losing
  the whole run; the result says where it stopped.
- **Marks from a combine or re-import no longer refuse a run.** The preview names
  them instead.
- **Tokens of truncated answers** reach the token cap and the process-wide ledger.
- **The split names labels left without a holdout**, the UI shows what the marks
  kept back, and the docs recommend splitting before balancing.
- **UI:** no preview while a run is in flight (it re-armed the run button); the
  result names what the checks discarded.
- **Small:** copies of shown or scrubbed examples count as duplicates; labels and
  cells from the data enter prompts as single lines; the source check ignores
  case; enrichment refuses the label column as a field.

## [Unreleased] — review of the balancing work (2026-09-16)

An independent review found 9 major, 11 minor and 6 small issues in the work
below; 24 were reproduced before fixing. One commit per concern, test-first. The
design decisions are recorded in `docs/plan-2026-09-13-balance-enrichment.md`.

### Fixed

- **Holdout leakage through examples.** Real rows shown to the generator are
  marked `example_for` and stay in training, like generated rows; the split
  reports the real rows it kept back, masks by position, and treats a missing
  cell as no mark.
- **Enrichment overwrote curated content.** A short list is extended instead of
  replaced (this was older than the generalisation), an empty answer changes
  nothing, and `min_values > 1` on a single-valued field is refused — it made
  every description a gap. The prompt now states which field to fill.
- **A second click ran unpreviewed into the result.** A run is sent only for the
  request its preview was made for; balancing never writes in place.
- **The preview understated the cost.** It applies `limit`, names the worst case
  including retries next to the call budget, and a run that cannot fit is refused
  before it pays.
- **Balancing:** verbatim copies of imported rows passed the duplicate gate;
  re-runs imitated earlier generated rows; phone numbers survived the PII scrub
  when a separator split them; one-character values were accepted; a batch of
  ten could be truncated at the default output limit; labels the limit cut were
  reported as complete; 5.5 s of pandas work ran on the event loop (now 0.13 s,
  off the loop).
- **Errors:** a truncated or schema-breaking model answer was a 500 or a 400
  quoting the output; an unconfigured purpose, an empty separator and an unknown
  `target_field` were 500s; any engine ValueError was reported as the caller's
  fault. Fields may not name the label column, a mark column, or one column
  twice.
- **Combine** dropped the marks of generated rows.
- **UI:** a 422 read "[object Object]"; the panel sent requests without text
  columns; the WLO keyword column now starts as a list; busy labels survive a
  language switch; the five-column preview scrolls in its own box on a phone.

## [Unreleased] — enrichment for any field, and a minimum per label (2026-09-13)

### Added

- **`POST /refine/{name}/balance` lifts under-represented labels to a minimum number
  of rows.** It generates what each short label is missing from that label's own
  richest rows, gating every item against the fields' `min_values` and against
  repetition. `dry_run: true` answers with the deficit, the model calls and the
  synthetic share per label before an LLM session is opened at all — this is the one
  refine operation whose cost scales with how unbalanced the data is.
- **Generated rows stay out of the holdout.** They carry `generated_for`, and the
  split keeps them on the training side and reports `generated_excluded`. Without
  that, a model is validated on text written from the examples it trained on.

### Changed

- **Enrichment works on any text field, not only title/description/keywords.** A
  request names the fields and which one to fill; a field that holds several values
  says so with its separator, so a semicolon-separated author list needs no new code.
  Requests in the old `mode` shape keep working: they fill the same columns with
  the same instructions, inside a prompt that now names its fields by column. The
  provenance mark in `enriched_fields` is now the column name, which is what
  identifies a field once the three roles are gone.

## [Unreleased] — imports the size of a real export (2026-09-13)

### Changed

- **The upload cap is 500 MB, not 20.** It bounds the COMPRESSED size, and the exports
  this app exists to refine are far past 20 MB: the WLO full export is ~130 MB gzipped
  (~1.4 GB parsed), so it was refused with a 413 before its format was ever read. The
  cap is still one number an operator lowers — `DATAPREP_MAX_UPLOAD_MB` — and an
  instance others can reach should. The ten-fold inflation ceiling is unchanged and
  still derived from it: real exports inflate about elevenfold, a bomb a thousandfold,
  so it is the bomb it stops. Give the process memory to match the cap you set.

## [Unreleased] — duplicate names (2026-09-11)

A check for problems with reused names found seven, all reproduced against
the running app before fixing; two were introduced by the audit remediation
below. One commit each, test-first.

### Changed
- **No write replaces an existing name unless asked to.** All twelve writers
  (table import; `target` of op, filter, join, split, enrich, combine;
  reference import; vocabulary import, manual, fetch; seed-set build) answer
  409 for a taken name and replace only with `overwrite: true`. Working in
  place (target equals source) needs no flag. The UI asks with the native
  confirm dialog and resends. Before this, a mistyped target destroyed
  another dataset, and rebuilding a seed set discarded its hand-edited and
  LLM-paid seeds.

### Fixed
- Names are bounded in UTF-8 bytes (200), not characters. The 100-character
  cap from the audit fix refused the 108-character names split derives from
  a valid target, made datasets already stored under such names impossible
  to load or delete, and still let 70 emoji (280 bytes, `ENAMETOOLONG` on
  Linux) through. Split checks both derived names before writing anything.
- A reused name no longer inherits the previous table's operation history:
  import starts empty, split and enrich carry the source's steps, combine
  starts with its own step.
- A push under a name api_v3 already has answers 409 with what to do, not
  502 "rejected the upload".
- Two writers to one name no longer share a temp file (startup reference
  import racing an upload of the same name raised `FileNotFoundError`).

### Fixed after an independent review of the above
- The 409 check is repeated at the write with no await in between, so a
  name taken while a request parsed, ran an op, fetched or waited for the
  LLM is still refused. The reference store checks and writes under a lock,
  so the startup import of a default reference never replaces an upload
  that finished first.
- Every body and form field that names a dataset, vocabulary, reference or
  seed set allows what the byte bound allows (200); a 105-character dataset
  from a split could not be worked on in place, joined or combined, and a
  vocabulary named after a long upload file could not seed a set.
- A push or prediction api_v3 refuses relays api_v3's reason (for example a
  name too long for api_v3) instead of a bare status code.
- Seed bootstrap and term refinement apply their LLM result to the set as it
  is after the call: an edit made meanwhile is kept, a set deleted meanwhile
  stays deleted (404). Not new in this series.
- Split and enrich read the source history before writing or paying for the
  LLM; re-splitting `p_train` as `p` counts as working in place.
- Startup deletes temp files an interrupted write left behind.
- The split-history test now fails if split drops the source's steps (it
  passed for the wrong reason).

## [Unreleased] — audit remediation (2026-09-11)

The 2026-09-11 code audit (`docs/audits/2026-09-11-audit.md`, overall 75
weighted, verdict Conditional) found no critical or high issues. Its
findings are resolved one commit each, test-first; the table at the end of
the report maps each finding to its commit.

### Security
- A non-ASCII `X-API-Key` header answered 500 — a `TypeError` inside the auth
  dependency, two tracebacks per request, no credentials needed. The key is
  compared as bytes and answers 401 (S1).
- Names in URL paths had no length bound; on the Linux image a 300-character
  name made `Path.exists()` raise `ENAMETOOLONG` (500). `safe_name` caps at
  100 characters like every body field and answers 400 (L3).
- The `.env.example` placeholder `change-me` is refused at startup, so a
  copied-but-unedited file cannot run with a key everyone can read (S2).
- Non-multipart request bodies are capped at `max_upload_mb` by
  `Content-Length`; uploads keep their streamed cap (API2).
- `pip-audit` checks `requirements.lock` in both workflows (D1).
- A test walks the OpenAPI path table and asserts every route except
  `/health` answers 401 without a key; sabotage-verified (T6).

### Fixed
- Seed sets, vocabularies, references and run state are written all or
  nothing through one helper (`app/atomic.py`), and a failed write leaves no
  `.tmp` behind. A truncated seed-set file no longer takes the whole Seeds
  listing down (L1).
- `/refine/{name}/enrich` answers 503 for a missing LLM key, like the seed
  routes (API1).
- The startup reference-import task is held on `app.state` so it cannot be
  garbage-collected mid-flight (L2).
- The skip link is translated like every other string (F1).

### Performance
- Table import, reference import and vocabulary fetch run off the event
  loop; `/health` and run polling no longer stall behind a large upload (P1).
- `GET /refine/datasets` reads a shape sidecar written at save time instead
  of re-reading column 0 of every table on every call; tables stored before
  the sidecar are still counted through the parser (P2).

### Changed
- `RunManager._generate` (154 lines, complexity 20) is split: `run_context.py`
  loads what a run needs, `runs.py` executes it; every function is now at or
  below complexity 10 (A1). One api_v3 target guard for push and predict (A3).
- README: install with `-c requirements.lock`; the security model names the
  missing rate limiter (the proxy's job), the body cap and the plain-http
  localhost push. CLAUDE.md lists the known file-size exceptions.

## [Unreleased] — review remediation of the table workbench (2026-09-10)

A structured review of stages A–C found 3 major, 7 minor and 2 nit issues in
the workbench. All twelve are resolved, one commit each; the review report is
in the session history and the reasoning in each commit.

### Fixed
- **Sniffing the format inside a gzip stream failed past 64 KiB.**
  `gzip.decompress` needs a complete stream, so the peek raised on every
  real-sized file and silently fell back to `csv.gz`; a JSONL export named
  `export.gz` then failed with a CSV error. A streaming decompressor now reads
  the first bytes from any slice.
- **A gzipped upload could inflate without limit.** The upload cap bounds the
  compressed size only, and a 20 MB bomb inflated to ~20 GB inside the single
  worker. Inflation now stops at ten times `max_upload_mb` and answers 400
  naming the ceiling.
- **The rule builder sent `007` as the number 7 for every operator**, so
  `id is 007` matched `7`, `07` and `7.0` as well — the trap `rules.py`
  documents, reintroduced one layer up. Only the ordered operators
  (`<`, `≤`, `>`, `≥`, between) coerce now; `is`, `is not` and the list
  operators send text.
- The join ceiling counts the rows a left, right or outer join actually keeps,
  not only the inner estimate, and its refusal names that number.
- A typo in `verbosity` or `reasoning_effort` fails when `config.yaml` loads,
  as `provider` already did, rather than as a 400 on the first LLM call.
- The row viewer refuses a column named twice; pandas would have returned two
  columns of one name and `to_dict` kept the last.
- A JSON key containing a dot that collides with a nested path is refused with
  the column named, instead of silently overwriting it.
- `describe()` reuses the dataset list it was just given: an import causes one
  list fetch instead of three. The profile's numeric range is localised like
  every other number. A join key or rename pair splits at the first `=` only.

- **A refine write is all or nothing.** The dataset and its operation history were written in place, so a crash or a full disk mid-write left a half file behind. Both now go through a sibling .tmp and os.replace, as run_store already did, and the history is one write instead of write-then-append -- so a failure can no longer leave the source steps under the target name, a history that lied about how the dataset was made.

### Changed
- One vectorised comparison per rule instead of four evaluated eagerly.
- The README's security model names the decompression ceiling and the
  accepted regex-backtracking risk — the 200-character cap bounds a pattern's
  size, not its backtracking, and Python's `re` has no timeout.

## [Unreleased] — the generic table workbench, stage C (2026-09-10)

Design: `docs/plan-2026-09-10-tabellen-werkbank.md`. Completes stages A and B below.

### Added
- **LLM provider selection** — `openai`, `b-api-openai` or `b-api-academiccloud`
  per purpose in `config.yaml`. The two gateway providers route through
  OpenEduHub's Bildungs-API, which is OpenAI-compatible in its wire format but
  authenticates with **`X-API-KEY`** rather than a bearer token. No new
  dependency: the existing client serves all three, and with it the budgets,
  the spend ledger, the retries and the test seam.
- **A Tables tab** making the stage A and B endpoints usable. Import with format
  options, a paginated searchable row viewer, the column profile, a rule builder
  for row filters, column keep/drop/rename, duplicate counting and removal over
  key columns, a join with a size check first, and export in four formats.
  German and English throughout.

### Changed
- **Default model is now `gpt-5.6-luna` with `verbosity` and `reasoning_effort`
  on `low`** (previously `gpt-5.4-mini` / `gpt-5.4-nano`). This app extracts and
  writes short text, where reasoning tokens cost time and money without
  improving the answer. Both controls are dropped for models that would answer
  400 — every vLLM-hosted AcademicCloud model takes the classic `max_tokens`
  body instead.
- `base_url` is unset by default so the provider derives it. Set it explicitly
  to point at a private gateway; it then wins.
- **The page is 1200px wide**, matching api_v3, because the row viewer's columns
  come from whatever file was imported. Help text keeps its own 72ch measure, so
  the wider page does not stretch prose.

### Fixed
- **Every file input was 23px tall and every checkbox label 24px-minus-one** —
  both a pixel under the WCAG 2.2 SC 2.5.8 target minimum. api_v3 had already
  fixed the file inputs; data-prep had not. Measured in the browser, and the two
  rules fix all four uploads and all five checkboxes, not only the new ones.
- **The i18n parity test carried a hardcoded list of eight JS modules**, so it
  passed without ever checking a ninth. It now derives the list from
  `index.html`'s script tags. Sabotage-verified: removing one German key makes it
  fail naming that key.

### Not built, and why
- **A provider *selector* in the UI.** The plan asked for one, but a control
  that sets the provider per request would contradict the boundary the provider
  exists behind: it decides which host the server calls out to, and a caller who
  can redirect that has an SSRF pivot. It stays in `config.yaml`, like
  `base_url`. The UI names the provider when it is not plain OpenAI, which is
  the part a reader actually needs.
- **A live `/models` list.** It would need an outbound proxy endpoint and the
  gateway on the fetch allowlist — real attack surface for a convenience, whose
  value was to catch the gateway's unannounced model renames. A 503 already
  reports those at the moment they matter.

### Refactored
- `llm.py` was at 317 lines, past the ~300 the constitution sets. Spend
  accounting moved to `llm_budget.py` and the exception types to `llm_errors.py`
  — the latter because both halves raise them and either importing the other
  would be a cycle. `llm.py` is now 247 lines. The first attempt at this split
  flattened `BudgetExceeded` from `LlmError` to `Exception` to break the cycle,
  which would have sent budget stops out as 500s past five `except LlmError`
  handlers; a test now pins the hierarchy.

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
