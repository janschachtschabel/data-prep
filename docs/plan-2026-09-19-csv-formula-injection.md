# Design: spreadsheet-safe CSV downloads (CSV/formula injection)

**Status: proposed 2026-09-19. Waiting for the owner to choose the default (see
"Decision").** This was found as a pre-existing issue in the review of 2026-09-19. It was
left out of that fix round on purpose, because it is a design decision.

## Goal

When a person opens a CSV downloaded from data-prep in Excel or LibreOffice, no cell runs
as a formula. The bytes api_v3 trains on (the push) and every CSV meant for programs stay
exactly as they are stored.

## Context

Every CSV writer in the app writes cell values unchanged. A cell that begins with `=`,
`+`, `-`, `@`, a tab or a carriage return becomes a live formula when a spreadsheet opens
the file (OWASP "CSV Injection"). For example, `=HYPERLINK("http://…","click")` in a
harvested title puts one click between the reader and an attacker's page, and older Excel
versions also run DDE payloads. Metadata harvested from the web can hold such values: titles,
descriptions, keywords, and also the column names of an imported JSON.

OWASP's fix is an apostrophe before such a cell. That apostrophe changes the text for
every program that reads the file back. api_v3's `clean_text` keeps it (read 2026-09-19:
`api_v3/app/data.py:49`), so the model would train on `'=HYPERLINK(…)`. The workbench's
own import keeps it too (`tabular._read_csv`, `dtype=str`).

## Inventory: every CSV that leaves the app

| # | Where | Code | Leaves as | Read by |
|---|---|---|---|---|
| 1 | `GET /runs/{id}/export.csv` (Runs tab: **CSV**) | `exporter.to_csv` | download | **both.** Its label is "api_v3 training CSV". People open it to look at it, upload it to api_v3 by hand, or import it back into the workbench |
| 2 | `POST /runs/{id}/push` | `exporter.to_csv` → `apiv3.push_csv` | upload to api_v3 `/datasets/import` | api_v3 only (pandas, trains on the text) |
| 3 | `GET /refine/{name}/download?format=csv\|csv.gz` (Tables tab: **Download**) | `tabular.write_table` | download | **both.** The default option is labelled "CSV, semicolon (api_v3)", and the docstring calls it "the semicolon CSV api_v3 reads" |
| 4 | `POST /refine/{name}/push` | `df.to_csv` in `routes/refine_prep.py` → `push_csv` | upload to api_v3 | api_v3 only |
| – | `…/download?format=json\|jsonl`, `GET /runs/{id}/export.jsonl`, `GET /runs/{id}/audit.md` | | download | not CSV. No spreadsheet evaluates formulas in them, so they are out of scope |
| – | refine store `data/refine/<name>.csv` (`refine/store.py`), reference sets (`reference.py`) | | internal storage, never served | pandas only. This *is* the data, and it stays raw |

The app writes no XLSX anywhere, and the UI builds no CSV in the browser: `Api.download`
only saves what the server sent.

**Consequence:** no download is meant only for people. Both downloads are also the files a
person hands to api_v3 or brings back into the workbench. To neutralise only what people
open, the app needs a download that exists only for people.

## Approaches

**A: an opt-in spreadsheet variant (recommended).** A new query flag,
`spreadsheet_safe` (default `false`), on downloads #1 and #3. The UI offers the variant
explicitly: a **CSV (Excel)** button in Runs and a checkbox in Tables. Without the flag,
every byte stays as it is today. The push never neutralises.
- Pros: nothing that exists today changes. That covers hand uploads to api_v3,
  re-imports, scripts, and hash comparisons (`write_table` promises byte-identical
  re-exports). The changed file is produced only on request and has its own name
  (`<name>.spreadsheet.csv`), so nobody mistakes it for the training file.
- Cons: the plain CSV is not safe by default. A person who opens it in Excel is not
  protected unless they choose the variant. The mitigation is that the UI offers the
  variant beside every CSV download and the guide says which file to use.

**B: opt-out.** Downloads are neutralised by default, `raw=true` returns the exact bytes,
and the push stays raw.
- Pros: people are protected by default.
- Cons: it silently changes the files labelled "api_v3". Someone who uploads the
  downloaded CSV to api_v3 by hand, or imports it back, then works and trains on `'=…`.
  That is exactly the silent change the review ruled out. It also changes behaviour for
  every existing API client, and every "(api_v3)" label in the UI would have to change.

**C: an XLSX export for people.** Text cells in XLSX are never formulas, so no apostrophe
is needed.
- Pros: people see the text exactly as it is, and Excel reads the encoding correctly.
- Cons: it needs openpyxl or xlsxwriter (a new dependency, against the dependency rule) or
  a hand-written OOXML writer. Excel's limits (32,767 characters per cell, 1,048,576 rows)
  also cut real WLO exports. The fix is bigger than the problem. This is a v2 candidate.

**Recommendation: A.** It is the only option that protects people without changing any
file that is already used as training data.

## Design (approach A)

### Neutralisation (OWASP)

- If a cell starts with `=`, `+`, `-`, `@`, a tab (0x09) or a carriage return (0x0D), the
  writer puts an apostrophe in front of it. All other cells stay unchanged.
- **Header cells get the same treatment.** Column names come from uploaded data, and a
  JSON key or CSV header can be `=HYPERLINK(…)`.
- **Every field is quoted** (`csv.QUOTE_ALL`). A spreadsheet may split on a separator
  other than `;`: Excel with an English locale splits on commas. Without quoting, the cell
  `x,=1+1` opens as two cells, and the second one is a formula. With quoting, a comma or tab
  inside a value can never start a new cell. The csv writer still doubles quotes inside
  values.
- **Numbers get no exemption.** `-5` becomes `'-5`, which is text. This is simpler and is
  what OWASP prescribes, and negative numbers are rare in these datasets.

### Files

- Create `app/spreadsheet.py` (about 35 lines): the triggers, `defuse` and
  `spreadsheet_csv`. It is a module of its own because both writers need it and
  `tabular.py` is already over its size budget.
- Modify `app/exporter.py` so that `to_csv` gains a `spreadsheet_safe` keyword. The
  default line stays literally unchanged.
- Modify `app/tabular.py` so that `write_table` gains a `spreadsheet_safe` keyword. It
  applies to `csv` and `csv.gz` and leaves JSON and JSONL unchanged.
- Modify `app/routes/exports.py` so that `export_csv` gains the query flag. The filename
  is `<run_id>.spreadsheet.csv` when the flag is set. `push` is unchanged.
- Modify `app/routes/tables.py` so that `download_dataset` gains the same flag. The
  filename is `<name>.spreadsheet.<format>` for the CSV formats.
- **Not modified:** `app/apiv3.py`, the push in `app/routes/refine_prep.py`, the refine
  store and references.
- UI changes:
  - `static/ui/runs.js` gets the **CSV (Excel)** button.
  - `static/ui/index.html` and `static/ui/tables.js` get the checkbox and the filename.
  - `static/ui/i18n.js` gets the German and English strings.
- Docs: README (the security model and the workbench table), CHANGELOG,
  `docs/ui-guide.md` (German) and TODO.md.

### Interfaces

```
app.spreadsheet.FORMULA_TRIGGERS: tuple[str, ...] = ("=", "+", "-", "@", "\t", "\r")
app.spreadsheet.defuse(value: str) -> str
app.spreadsheet.spreadsheet_csv(frame: pd.DataFrame, *, sep: str) -> str
app.exporter.to_csv(samples, vocab, *, label_column=DEFAULT_LABEL_COL, spreadsheet_safe=False) -> str
app.tabular.write_table(df, *, fmt="csv", separator=";", spreadsheet_safe=False) -> bytes
GET /runs/{run_id}/export.csv?spreadsheet_safe=true
GET /refine/{name}/download?format=csv|csv.gz&spreadsheet_safe=true
```

```python
def spreadsheet_csv(frame: pd.DataFrame, *, sep: str) -> str:
    safe = frame.map(lambda cell: defuse(cell) if isinstance(cell, str) else cell)
    safe.columns = [defuse(str(name)) for name in safe.columns]
    return safe.to_csv(sep=sep, index=False, lineterminator="\n", quoting=csv.QUOTE_ALL)
```

### Non-functional

- **Performance** (measured 2026-09-19 with pandas 3.0.3): the cellwise pass costs about
  2.3 s per 8 million cells, which is about what `to_csv` itself costs. Vectorised
  variants were not faster. The pass runs only on request, in the worker thread that
  `write_table` already uses.
- **Dependencies:** none new (the standard library's `csv` and pandas).
- **Security:** see the known limits below.

### Known limits (documented, not fixed)

- A cell with spaces before a trigger (` =1+1`) gets no apostrophe. Excel reads it as
  text. LibreOffice evaluates it only if the person turns on both "Trim spaces" and
  "Evaluate formulas" in its import dialog.
- The plain CSV, which is the default, stays unprotected by design, because it is the
  training file.
- Excel still guesses the encoding of a UTF-8 CSV without a BOM, which garbles umlauts.
  A BOM for the spreadsheet variant is a possible follow-up and not part of this fix.

## Decision (owner)

1. Default: **A (opt-in, recommended)** or **B (opt-out)**.

## Tasks (for approach A)

**Phase 1, core.** Step 0: invoke `/better-coding-workflow`.
- **T1** `tests/test_spreadsheet.py`: write failing tests first. They cover the
  `=HYPERLINK` cell being defused, each trigger, harmless cells left unchanged, the header,
  and quoting that stops the comma vector. Then implement `app/spreadsheet.py` until the
  tests pass.
- **T2** `exporter.to_csv`: write a failing test first. With `spreadsheet_safe=True` the
  `=HYPERLINK` title gets its apostrophe; by default it stays raw. Then implement until
  the test passes.
- **T3** `tabular.write_table`: write failing tests first. `csv` and `csv.gz` are defused,
  `jsonl` stays unchanged, and the default stays raw. Then implement until the tests pass.

**Phase 2, routes.** Step 0: invoke `/better-coding-workflow`.
- **T4** The run export route: write a failing test first. The flag produces a defused
  file with the `.spreadsheet.csv` filename, the default stays raw, and the push body
  equals the default `to_csv`. Then implement until the test passes.
- **T5** The table download route: write a failing test first. The flag produces a
  defused file with its filename, and the default stays raw. Add a pin that the refine push
  sends the cell raw. Then implement until the tests pass.

**Phase 3, UI.** Step 0: invoke `/better-coding-workflow` and `/better-coding-frontend`.
- **T6** Add the Runs button, the Tables checkbox with its filename, and the German and
  English strings. Pin them in `test_ui.py`.

**Phase 4, docs and verification.**
- **T7** Update the README, CHANGELOG, `docs/ui-guide.md` and TODO.md.
- **T8** Run the full pytest suite, ruff and mypy, and `node --check` on the changed JS.

## Verification

- Every new test fails before its implementation and passes after it. The first run's
  output is the evidence.
- The full pytest suite passes, and ruff and mypy report no errors.
- Tests pin that the default download and both pushes still carry the `=HYPERLINK` cell
  unchanged.
