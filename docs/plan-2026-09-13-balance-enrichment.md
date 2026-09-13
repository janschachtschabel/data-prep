# Design: generic field enrichment and per-label balancing

## Goal

Bring an unevenly distributed dataset up to a stated minimum number of rows per
label by generating new rows with an LLM, grounded in the rows that already carry
that label — and make the existing gap-filling enrichment work on whichever text
fields the user selected, including fields that hold a list of values.

## Context

`refine/enrich.py` fills gaps in **existing** rows today: an empty description, too
few keywords. It knows exactly three roles (`title_col`, `description_col`,
`keyword_col`) and two modes, and its two prompts are hard-coded for them. Nothing
creates rows, so a label with 7 examples stays at 7.

`refine/prep.py::balance_report` already answers the other half — it returns
`label_support` and `labels_below_recommended` — and `generation.py` already owns
the machinery for writing new items well: prompt recipe, an `accept_item` gate with
a description-length corridor, exact dedupe and a PII scrub. It is aimed at
vocabulary concepts, not at the labels of an existing dataset.

So this is mostly a join of parts that exist, plus one genuinely new idea: a field
may hold a **list**, and a generator has to produce and merge lists, not strings.

## Scope

In scope:

- A field **specification** per text column: name, role hint, and whether it is
  multi-valued with which separator.
- `enrich_dataset` generalised from three fixed roles to that specification.
- A **balance** operation: for every label under a target, generate rows until it
  reaches the target, using that label's existing rows as few-shot examples.
- Provenance on every generated row, and exclusion of generated rows from the
  holdout split.
- UI: the field specification, the target number, a preview of what would be
  generated (labels, counts, estimated LLM cost) before anything is paid for.

Out of scope:

- Changing how `api_v3` trains. Balancing produces a dataset; what a run does with
  it is unchanged.
- Generating rows for a label with **zero** existing rows. Without an example the
  hybrid approach has nothing to ground in; `labels_below_target` reports them and
  the operation skips them with a reason. (The vocabulary-based generator already
  covers "make rows from a concept name alone" and stays the tool for that.)
- Balancing across a **multi-label** dataset as an optimisation. Each deficient
  label is filled independently; a generated row carries exactly the label it was
  generated for. Rows that happen to carry several labels still count toward each,
  as `balance_report` already does.
- Down-sampling over-represented labels.

## Approach

Three approaches were weighed for creating the missing rows; the user chose **A**.

**A — Hybrid, existing rows as few-shot (chosen).** For each deficient label, take
up to `k` of its existing rows, put them in the prompt as examples, and ask for new
items in the same register. Grounded in the real distribution, needs ≥1 example.

**B — From the label name alone.** What the vocabulary generator does. Works at
zero examples, drifts from the dataset's own voice, and tends to prototype collapse
(the reason `generation.py` carries an "avoid these titles" block).

**C — Paraphrase existing rows.** Closest to the originals, but at 3 rows for a
target of 100 it repeats itself into uselessness and adds no new vocabulary for the
classifier to learn from.

A is B's machinery with C's grounding: reuse `build_generation_prompt`'s structure
and `accept_item`'s gates, swap the concept framing for label + examples.

## Global constraints

From `data-prep/CLAUDE.md`, verbatim where they bind this work:

- Comments, docstrings and API texts in English; comments explain *why*.
- Test-first: failing test → fix → green. Never weaken a test to pass.
- Files stay under ~300 lines; split by responsibility.
- Env prefix `DATAPREP_`; secrets only from env, never logged, never in `config.yaml`.
- Every user-facing string goes through the i18n layer, both languages, semantic keys.
- No pickle; user-supplied names through `security.safe_name`.
- LLM output is PII-scrubbed as defense in depth, whatever the prompt asked.

## Architecture

### Files

| File | Responsibility |
|---|---|
| `app/refine/fields.py` *(new, ~90 lines)* | The `TextField` specification and the list-aware read/merge helpers. One reason to change: how a field is described. |
| `app/refine/enrich.py` *(modify)* | Gap-filling, now over a list of `TextField` instead of three fixed roles. |
| `app/refine/balance.py` *(new, ~150 lines)* | The balancing engine: which labels are short, build the prompt from examples, accept, assemble rows. |
| `app/routes/refine_prep.py` *(modify)* | `POST /refine/{name}/balance` + its request model; `EnrichRequest` gains the field specification. |
| `app/refine/prep.py` *(modify, ~10 lines)* | `holdout_split` keeps generated rows out of the holdout. |
| `app/static/ui/index.html`, `refine.js`, `i18n.js` *(modify)* | Field specification UI, target input, preview, both languages. |
| `tests/test_refine_fields.py`, `tests/test_refine_balance.py` *(new)* | The two new units. |
| `tests/test_refine_enrich.py`, `tests/test_refine_prep.py`, `tests/test_ui.py` *(modify)* | Generalisation, holdout exclusion, UI. |

### Data model

```python
@dataclass(frozen=True)
class TextField:
    """One text column and how its cell is read.

    ``separator`` set means the cell holds a LIST: keywords are the reason, but a
    field of ISO codes or a semicolon-separated author list behaves the same, so the
    list-ness belongs to the field rather than to a keyword special case.
    """
    column: str
    separator: str | None = None      # None = one value; "," = list
    min_values: int = 1               # a list below this counts as a gap
    guidance: str = ""                # one line the prompt shows for this field
```

The generated rows are marked in two existing-style columns, both written by this
feature and never overwritten:

- `enriched_fields` — already exists, comma-separated field names (gap filling).
- `generated_for` — **new**, the label a row was generated for; empty for real rows.
  This is what `holdout_split` reads, what the UI counts, and what makes a later
  audit possible. A dataset without the column behaves exactly as today.

### Data flow

```
balance(name, fields, label_column, target, limit)
  └─ balance_report(df, …)                     → label_support, labels below target
  └─ for each deficient label (ascending support):
       ├─ examples  = up to k rows carrying it (longest text first, deduped)
       ├─ prompt    = build_balance_prompt(label, examples, fields)
       ├─ items     = await complete(prompt, BalanceBatch)   # batched, budget-aware
       ├─ accept    = accept_item(…)  length corridor · exact dedupe · PII scrub
       └─ rows      = one DataFrame row per accepted item, generated_for = label
  └─ concat(df, new_rows) → save as NEW dataset (never in place)
  └─ write_ops(target, [*history, {"op": "balance", …}])
```

### Interfaces

```python
# app/refine/fields.py
def read_values(cell: object, field: TextField) -> list[str]: ...
def write_values(values: list[str], field: TextField) -> str: ...
def is_gap(cell: object, field: TextField) -> bool: ...

# app/refine/balance.py
async def balance_dataset(
    df: pd.DataFrame, *, fields: list[TextField], label_column: str,
    target_per_label: int, complete: Complete, label_separator: str = ",",
    examples_per_label: int = 4, batch_size: int = 10, limit: int = 500,
) -> tuple[pd.DataFrame, dict]: ...
    # returns (new frame, {"target", "labels_filled", "rows_added",
    #                      "skipped_without_examples": [...], "per_label": {...}})

# app/refine/enrich.py  (generalised)
async def enrich_dataset(
    df: pd.DataFrame, *, fields: list[TextField], target_field: str,
    complete: Complete, limit: int = 5000,
) -> tuple[pd.DataFrame, dict]: ...
```

`Complete` is the existing injected callable — the engines stay testable without a
model, as `enrich_dataset` already is.

### Dependencies

None. Everything used here — pandas, pydantic, the LLM session, the PII scrub — is
already in `requirements.lock`.

## Non-functional

- **Cost.** Every call goes through `session_for(purpose)` and the existing budget;
  `BudgetExceeded` surfaces as 429 exactly as enrich does today. `limit` caps rows
  per call. The preview states the number of rows and batches **before** the first
  call, because this is the one operation whose cost scales with how unbalanced the
  data is.
- **Honesty of later metrics.** `holdout_split` excludes rows with a non-empty
  `generated_for`. Without that, a model is validated on text an LLM wrote from the
  same examples it trained on, and the F1 flatters itself. The split's stats gain
  `generated_excluded` so the number is visible, not implied.
- **Security.** `accept_item`'s PII scrub applies to every generated cell; names
  through `safe_name`; no new endpoints without the existing key dependency.
- **i18n.** Every new string in both tables, semantic keys (`refine.balance.*`).
- **UI floor.** The preview is the empty/loading/error case: a disabled button while
  running, a result table after, an explicit "nothing to do" when no label is short.

## Risks

| Risk | Mitigation |
|---|---|
| Generated rows drown the real ones (a label at 3 → 100 is 97 % synthetic) | The preview states the ratio per label; the result stats carry it; the docs say plainly that a label lifted from 3 is a label the model has barely seen. |
| Prototype collapse — 97 near-identical items | Reuse `generation.py`'s exact-dedupe and the "avoid these titles" block, seeded with what was already generated in this run. |
| A list field generated as prose ("keywords: Mathe und Algebra") | The field's `separator` goes into the prompt AND the parse: `write_values` re-joins what the model returned as a list, so a malformed answer degrades to one value rather than a broken cell. |
| Cost surprise on a 300-label dataset | `limit` defaults to 500 rows per call and the preview shows the estimate first. |
| `generated_for` colliding with a user column of that name | `balance` refuses to run when the column exists with different semantics (non-empty values in rows it did not write). |

## Open questions

None. The four design decisions were settled before this document:
hybrid generation · synthetic rows never in the holdout · per-field separator ·
UI gaps (1+2) shipped first, already done in `ec383cd`.

---

# Tasks

## Phase 1 — the field specification (no behaviour change yet)

Step 0: invoke `/better-coding-workflow`.

### Task 1: `TextField` and its list helpers

**Files:** Create `app/refine/fields.py`; create `tests/test_refine_fields.py`.

**What:** The dataclass above plus `read_values` / `write_values` / `is_gap`. A cell
of a single-valued field reads as `[]` when blank and `[value]` otherwise; a list
field splits on its separator, strips, drops empties. `write_values` joins with
`separator + " "` for lists and takes the first value otherwise.

**Test first (the cases that matter):** a blank cell, a list cell with stray spaces
and an empty item, a single-valued cell containing the separator (must stay one
value), `is_gap` for `min_values=3` at two values.

**Verification:** `python -m pytest tests/test_refine_fields.py -q` → passes.

### Task 2: `enrich_dataset` over fields

**Files:** Modify `app/refine/enrich.py`, `tests/test_refine_enrich.py`.

**What:** Replace the three role parameters with `fields: list[TextField]` and
`target_field: str`; build the prompt from the other fields' current values plus the
target field's `guidance`. Keep `enriched_fields` marking and the PII scrub
unchanged. The two shipped prompts become the default `guidance` of the WLO fields
so today's behaviour is reproduced, which the existing tests pin.

**Verification:** the existing enrich tests pass unchanged in behaviour (they are
rewritten to the new signature, same assertions), plus a new test that a
**third** field (e.g. a semicolon-separated list) enriches without any code change.

### Task 3: the route speaks fields

**Files:** Modify `app/routes/refine_prep.py`.

**What:** `EnrichRequest` gains `fields: list[FieldSpec]` (pydantic mirror of
`TextField`) and `target_field`, keeping the old three column parameters as an
accepted legacy shape that maps onto the new one, so an existing client keeps working.

**Verification:** `python -m pytest tests/test_routes*.py -q`; a request in the old
shape and one in the new shape produce the same dataset.

## Phase 2 — balancing

Step 0: invoke `/better-coding-workflow`.

### Task 4: which labels are short, and what would it cost

**Files:** Create `app/refine/balance.py` with `plan_balance(df, …) -> dict`;
create `tests/test_refine_balance.py`.

**What:** Pure function over `balance_report`: given a target, return per label the
current support, the deficit, whether it has examples at all, and the resulting row
and batch counts. No LLM, no I/O — this is what the preview shows.

**Test first:** a label above target (deficit 0), one below (deficit = target −
support), one with zero rows (reported under `skipped_without_examples`), the batch
count for `batch_size=10`.

### Task 5: the prompt from examples

**Files:** Modify `app/refine/balance.py`; extend `tests/test_refine_balance.py`.

**What:** `build_balance_prompt(label, examples, fields, avoid_titles)` — the label
in words, up to `k` examples rendered field by field (lists joined by their own
separator), the field guidance, and the "avoid these titles" block seeded with the
titles already produced in this run.

**Test first:** the prompt contains every example's text, names each field once, and
lists the avoided titles; a list field appears joined by its separator, not as a
Python list.

### Task 6: generate, accept, assemble

**Files:** Modify `app/refine/balance.py`; extend `tests/test_refine_balance.py`.

**What:** `balance_dataset` as specified above, with a scripted `complete` in the
tests. Rejected items (too short, duplicate) are retried within the batch budget and
then given up on, with the reason in the stats.

**Test first:** a label at 2 with target 5 yields exactly 3 new rows, each with
`generated_for` set and `enriched_fields` empty; a duplicate item from the model is
dropped; a label already at target is untouched; `limit` caps the total.

### Task 7: the holdout never sees a generated row

**Files:** Modify `app/refine/prep.py`; modify `tests/test_refine_prep.py`.

**What:** `holdout_split` treats rows with a non-empty `generated_for` as
train-only, and reports `generated_excluded` in its stats. A frame without the
column behaves exactly as today.

**Test first:** a frame with 10 real and 10 generated rows puts zero generated rows
in the holdout and says so in the stats; a frame without the column splits as before
(the existing assertions stay).

### Task 8: the route

**Files:** Modify `app/routes/refine_prep.py`; modify `tests/test_routes_refine*.py`.

**What:** `POST /refine/{name}/balance` with `BalanceRequest` (fields, label column,
target, limit, target name, overwrite, llm purpose), mirroring the enrich route's
error mapping: 400 unknown column, 429 budget, 502/503 LLM, `refuse_existing`
before and after the calls.

**Verification:** a scripted-LLM route test writes a new dataset and leaves the
source untouched.

## Phase 3 — UI

Step 0: invoke `/better-coding-workflow` **and** `/better-coding-frontend`.

### Task 9: the field specification in the form

**Files:** Modify `app/static/ui/index.html`, `refine.js`, `i18n.js`; modify
`tests/test_ui.py`.

**What:** Each chosen text column gets a row: name (read-only), a "holds a list"
checkbox, and a separator input enabled by it. Built from the columns the chips
already offer, so nothing new has to be typed.

### Task 10: target, preview, run

**Files:** Modify the same three; modify `tests/test_ui.py`.

**What:** A target-per-label number input, a "preview" button calling `plan_balance`
and rendering the per-label table with the totals and the generated share, and a run
button disabled until a preview exists.

**Verification:** `python -m pytest tests/test_ui.py -q`; the i18n completeness tests
already in that file cover both languages automatically.

## Verification plan

| Requirement | How | Success |
|---|---|---|
| Fields generic, lists supported | `tests/test_refine_fields.py` | list and single-value cells round-trip |
| Enrich unchanged for WLO defaults | rewritten `tests/test_refine_enrich.py` | same assertions as today |
| Deficit computed correctly | `tests/test_refine_balance.py` | per-label deficits and batch counts |
| Rows generated to target | same | exactly `target − support` new rows |
| Provenance | same | every new row carries `generated_for` |
| Holdout honesty | `tests/test_refine_prep.py` | zero generated rows in holdout |
| No behaviour lost | `python -m pytest tests -q` | 601 passing today, all still pass |
| Live | one balance run against the container with a scripted target | the new dataset appears, source untouched |

**Regression risk:** `holdout_split` and `enrich_dataset` are used by existing
routes and tests; both keep their current behaviour for frames and requests that do
not use the new parameters, and their existing tests stay in place unchanged as the
proof.
