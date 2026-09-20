"""Stage 8 — exports and the audit report.

THE SEPARATION GUARANTEE LIVES HERE: this module reads exclusively from a
run's ``samples.jsonl`` (statuses ``passed``/``approved``) and has NO import
path to reference data — reference rows can structurally never reach an
export. Tests pin this both behaviorally and via AST import analysis.
"""

from __future__ import annotations

import json
from pathlib import Path

import pandas as pd

from .refine.provenance import GENERATED_FOR
from .samples import read_samples
from .vocab import Vocabulary

EXPORTABLE_STATUSES = ("passed", "approved")

TITLE_COL = "properties.cclom:title"
DESC_COL = "properties.cclom:general_description"
KEYW_COL = "properties.cclom:general_keyword"
DEFAULT_LABEL_COL = "properties.ccm:taxonid"

# Mandatory usage notes — the audit report of a synthetic dataset must never
# ship without them (design-doc risk table: distribution shift).
_WARNING_BLOCK = """## ⚠️ Nutzungshinweise (Pflicht)

- **Evaluation nur mit echten, kuratierten Daten.** Der Testsplit darf niemals
  synthetische Zeilen enthalten — interne Metriken auf Mischdaten überschätzen
  die reale Qualität (im Projekt gemessen: intern +0.07 vs. real −0.075).
- **Mischtraining empfohlen:** synthetische Daten ergänzen echte Daten, sie
  ersetzen sie nicht. Bei rein synthetischem Training ist der
  Distribution-Shift maximal.
- Alle Zeilen tragen `source=synthetic` und `generated_for=<Konzept>` und sind
  PII-gescrubbt (E-Mail, Telefon, URL, Handle maskiert); Personennamen werden in
  v1 nicht erkannt. `generated_for` hält sie aus jedem Holdout von data-prep und
  aus der Validierung von api_v3 heraus, solange der Datensatz genug echte Zeilen
  zum Validieren hat. Ein reiner Lauf-Export hat keine: api_v3 misst dann über
  alle Zeilen und sagt das (`validated_on: all_rows`).
"""


def load_samples(run_dir: Path, statuses: tuple[str, ...] = EXPORTABLE_STATUSES) -> list[dict]:
    """All exportable samples of a run, in generation order (corrupted/partial
    lines from a crash are skipped, never a hard failure of the whole export)."""
    return [s for s in read_samples(run_dir / "samples.jsonl") if s.get("status") in statuses]


def to_jsonl(samples: list[dict]) -> str:
    """The run's samples, one JSON record per line -- each marked ``generated_for`` its
    concept, like the CSV: a JSONL export imported into the workbench would otherwise
    pass the split as real rows."""
    return "\n".join(json.dumps({**s, GENERATED_FOR: s["concept"]}, ensure_ascii=False)
                     for s in samples) + ("\n" if samples else "")


def to_csv(samples: list[dict], vocab: Vocabulary, *, label_column: str = DEFAULT_LABEL_COL) -> str:
    """Semicolon/UTF-8 CSV in the api_v3 training schema; the concept URI lands
    in ``label_column`` and its display name (from the vocabulary) next to it.

    Every row is marked ``generated_for`` its concept. ``source`` says the same, but
    combine overwrites it with the name the user gives the source; the mark is what
    combine carries, the split keeps out of a holdout, and api_v3 never validates on.
    """
    rows = [
        {
            TITLE_COL: s["title"],
            DESC_COL: s["description"],
            KEYW_COL: s["keywords"],
            f"{label_column}_DISPLAYNAME": vocab.label(s["concept"]),
            label_column: s["concept"],
            "source": "synthetic",
            GENERATED_FOR: s["concept"],
        }
        for s in samples
    ]
    frame = pd.DataFrame(
        rows,
        columns=[TITLE_COL, DESC_COL, KEYW_COL, f"{label_column}_DISPLAYNAME", label_column, "source",
                 GENERATED_FOR],
    )
    return frame.to_csv(sep=";", index=False, lineterminator="\n")


def audit_markdown(state: dict, samples: list[dict], vocab: Vocabulary) -> str:
    """Human-readable audit report for one run (German — it is editorial-facing)."""
    params = state["params"]
    counters = state["counters"]
    sims = [s["meta"].get("sim_max") for s in samples if s.get("meta", {}).get("sim_max") is not None]
    mean_sim = sum(sims) / len(sims) if sims else 0.0
    max_sim = max(sims) if sims else 0.0

    per_concept_rows = "\n".join(
        f"| {vocab.label(uri)} | {counters['per_concept'].get(uri, 0)} | {params['per_concept']} |"
        for uri in params["concepts"]
    )
    discard_rows = "\n".join(
        f"| {key} | {counters.get(key, 0)} |"
        for key in ("discarded_length", "discarded_duplicate", "discarded_leakage",
                    "discarded_semantic_duplicate")
    )
    return f"""# Audit-Report · Lauf {state['id']}

Erstellt: {state['created_at']} · Status: {state['status']}
Seed-Set: {params['seed_set']} · Vokabular: {params['vocab']} · Längenprofil: \
{params['length_profile']} ({params['corridor'][0]}–{params['corridor'][1]} Zeichen)

{_WARNING_BLOCK}

## Ergebnis

- Exportierbare Samples (passed/approved): **{len(samples)}** (Ziel: {params['target']})
- LLM-Aufrufe: {state['usage']['calls']} · Tokens: {state['usage']['tokens_total']}
- Diversität: mittlere sim_max **{mean_sim:.2f}**, Maximum {max_sim:.2f} \
(kleiner = vielfältiger; Dedupe-Schwelle liegt bei den Gates)

## Verworfen je Grund

| Grund | Anzahl |
|---|---|
{discard_rows}

## Je Konzept

| Konzept | erzeugt | Ziel |
|---|---|---|
{per_concept_rows}
"""
