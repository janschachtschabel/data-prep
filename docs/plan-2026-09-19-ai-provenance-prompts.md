# Design: AI provenance end to end, and prompts that know the dataset

**Status: requested 2026-09-19 ("plane die Anpassung und beginne mit der Umsetzung").**
Companion to api_v3's `docs/plans/2026-09-19-ai-marked-rows.md`, which reads the marks
this app writes.

## Goal

Every row an LLM wrote or touched carries a mark that survives until training, no such row
ever reaches a holdout, and the balancing and enrichment prompts tell the model what the
dataset looks like: what kind of field each column is, how long its entries typically are,
and which other labels the new text must not resemble.

## Context — what the prompts do today (read 2026-09-19)

Balancing (`refine/balance_prompt.py`) generates ALL selected text fields of a row; the
code sets the label column and `generated_for`. The prompt gives: an education-catalogue
context and the label VALUE (a URI such as `…/discipline/380` in the WLO export — the model
then learns the subject only from the examples); per field the column name, the user's
guidance line and, for a list, its separator; up to four real rows of the label as
examples, cut to 400 characters per cell; rules (different subtopics, "orient on the length
and tone of the examples", avoid existing titles, no PII).

Missing: (1) explicit lengths from the dataset — and the examples are picked LONGEST first
(`balance_gates.pick_examples`), so the one length signal points at the long end of the
label; (2) the minimum number of values of a list field, which the gate then enforces
after the call was paid for; (3) any contrast with the other labels; (4) a readable label
name. The UI's default guidance hard-codes "100-400 Zeichen" and "3-6" Schlagwörter,
numbers not taken from the data.

Enrichment (`refine/enrich.py`) shows the row's other fields, the field's shape (one value /
a list with a minimum) and the guidance — but neither the row's label nor a length.

Provenance: the split keeps `generated_for` and `example_for` rows out of the holdout, not
`enriched_fields` rows; a Runs export carries `source=synthetic` only, which `combine`
overwrites with the user's source name.

## Decisions (made here; the owner can overrule each)

1. **Every AI mark is train-only in the split**, `enriched_fields` included — the owner
   agreed to "der Split behandelt jede KI-Markierung als reines Training". Cost: a dataset
   enriched throughout has a small holdout; the split's stats say so.
2. **Runs exports write `generated_for=<concept>`.** Exports are rebuilt from the run on
   every download/push, so re-exporting marks old runs too; CSVs downloaded before this
   version stay unmarked (said in the changelog).
3. **Lengths come from the dataset:** per field, the middle half (25th–75th percentile) of
   the label's real rows (at least 5 filled cells), else of all real rows, else nothing is
   said. Characters for a single value, number of values for a list. A length written into
   the field's guidance takes precedence — so a request in the old `mode` shape keeps the
   instructions it always carried.
4. **Examples are representative, not the longest:** complete rows first, closest to the
   label's median length.
5. **Contrast by naming the other labels**, display names where the dataset has a
   `<label>_DISPLAYNAME` column: labels that co-occur with this one first, then by support,
   at most 30. No new gate — the existing label audit (api_v3 predictions) is the check
   for rows that read like another label.
6. **Enrichment names the row's label(s)** so the added text fits the classification.
   Enriched rows are train-only, so this cannot flatter an evaluation.

## Scope

In: split, exporter, balancing prompt context, example choice, output budget, enrichment
prompt context, UI default guidance without fixed numbers, docs.
Out: a new discrimination gate; recognising legacy `source=synthetic` rows; UI changes
beyond the guidance defaults and result texts.

## Architecture

| file | change |
|---|---|
| `app/refine/prep.py` | `enriched_fields` joins `train_only` |
| `app/exporter.py` | `generated_for` column |
| `app/refine/prompt_context.py` (new) | what a prompt learns from the frame: display names, contrast labels, typical shapes (`FieldShape`, `typical_phrase`) -- shared by balancing and enrichment |
| `app/refine/balance_prompt.py` | label name, contrast block, per-field kind and length |
| `app/refine/balance_gates.py` | representative `pick_examples`; budget from the typical length |
| `app/refine/balance.py` | computes the context once per run, passes it per label |
| `app/refine/enrich.py` | label context + typical length of the target field |
| `app/routes/refine_prep.py` | passes the label column/separator to enrichment |
| `app/static/ui/refine-fields.js`, `refine.js`, `i18n.js` | guidance defaults, split text |
| `docs/ui-guide.md`, `CHANGELOG.md`, `TODO.md` | docs |

```python
# app/refine/prompt_context.py
@dataclass(frozen=True)
class FieldShape:
    chars: tuple[int, int] | None    # 25th-75th percentile of a filled cell's length
    values: tuple[int, int] | None   # same for the number of values (list fields only)

def display_names(df, label_column: str, separator: str) -> dict[str, str]
def contrast_labels(label: str, rows_by_label: dict[str, list[int]], names: dict[str, str],
                    *, cap: int = 30) -> tuple[list[str], int]   # names, how many more
def field_shapes(cells: list[list[str]], fields: list[TextField]) -> list[FieldShape]

# app/refine/balance_prompt.py
def build_balance_prompt(label, examples, fields, *, n, avoid_titles,
                         label_name: str | None = None, others: tuple[list[str], int] = ([], 0),
                         shapes: list[FieldShape] | None = None) -> str
def output_budget(examples, fields, n, shapes: list[FieldShape] | None = None) -> int

# app/refine/enrich.py
async def enrich_dataset(..., label_column: str | None = None, label_separator: str = ",")
```

## Tasks

Phase 1 — provenance
- Step 0: invoke /better-coding-workflow
- 1.1 split: `enriched_fields` rows (and their text group) stay in training — test first in
  `tests/test_refine_prep.py`; result text and guide mention enrichment.
- 1.2 exporter: `generated_for` = concept on every row — test first in `tests/test_export.py`.

Phase 2 — balancing prompt
- Step 0: invoke /better-coding-workflow
- 2.1 `prompt_context.py` + tests: display-name pairing (single label, equal counts,
  mismatch → no name); contrast order (co-occurrence, then support), cap and remainder;
  shapes from the label's rows, dataset fallback, silence below the minimum.
- 2.2 prompt: tests assert the label name, the contrast rule, "EIN Wert" / list with
  minimum, the typical lengths, one-line sanitising of names.
- 2.3 `pick_examples`: complete rows closest to the median first — test first.
- 2.4 wiring in `balance.py` + budget; existing balance tests stay green.

Phase 3 — enrichment prompt
- Step 0: invoke /better-coding-workflow
- 3.1 label names and typical length in the enrichment prompt; route passes the label
  column — test first.
- 3.2 UI guidance defaults without fixed numbers (the old `mode` constants keep theirs).

Phase 4 — docs, review, verify
- Step 0: invoke /better-coding-workflow
- 4.1 `docs/ui-guide.md`, `CHANGELOG.md`, `TODO.md`.
- 4.2 /better-coding-review; /better-coding-verify: pytest, ruff, mypy,
  `node tests/ui/balance_panel.check.js`; container rebuild and one short real balancing
  run to see that the lengths and the contrast are followed.

## Acceptance criteria

1. A split never puts a row with any AI mark (or its text) into the holdout.
2. A Runs export carries `generated_for` on every row; api_v3 recognises it.
3. The balancing prompt names the label readably, lists the contrast labels, and states per
   field its kind and the dataset's typical length; examples are complete, typical rows.
4. The enrichment prompt names the row's label and the field's typical length.
5. Gate green: pytest, ruff, mypy, UI check script.
