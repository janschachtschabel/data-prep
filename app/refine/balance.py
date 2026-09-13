"""Refine balancing: bring every label up to a minimum number of rows.

Real WLO data is unevenly distributed — a few hundred rows for one subject, three
for another — and a label the model has seen three times is a label it cannot
learn. This module generates the missing rows for each short label FROM THAT
LABEL'S OWN EXAMPLES, so the new text resembles the material the label actually
describes rather than a generic paragraph about the subject.

``plan_balance`` is the preview and stays pure: it answers what would be generated
and how many model calls it would take, before anything is paid for. Generation
itself lives in ``balance_dataset`` below, the prompt it uses in
``balance_prompt.py``.
"""

from __future__ import annotations

import math
from collections import defaultdict
from collections.abc import Awaitable, Callable

import pandas as pd
from pydantic import BaseModel

from ..pii import scrub
from ..textnorm import split_labels
from .balance_prompt import BalanceBatch, BalanceItem, build_balance_prompt
from .fields import TextField, read_values, write_values

Complete = Callable[[str, type[BaseModel]], Awaitable[BaseModel]]

def _rows_by_label(
    df: pd.DataFrame, label_column: str, label_separator: str
) -> dict[str, list[int]]:
    """Positional row indices per label, in the order the rows appear."""
    rows: dict[str, list[int]] = defaultdict(list)
    for position, cell in enumerate(df[label_column]):
        for label in set(split_labels(cell, label_separator)):
            rows[label].append(position)
    return rows


def _has_text(row: pd.Series, fields: list[TextField]) -> bool:
    """Does this row carry anything a prompt could show as an example?"""
    return any(read_values(row.get(field.column), field) for field in fields)


def plan_balance(
    df: pd.DataFrame,
    *,
    fields: list[TextField],
    label_column: str,
    target_per_label: int,
    label_separator: str = ",",
    batch_size: int = 10,
) -> dict:
    """What balancing to ``target_per_label`` would generate, and what it would cost.

    Returns ``{target, rows_to_add, batches, labels_below_target,
    skipped_without_examples, per_label}``; ``per_label`` carries the support, the
    deficit, the batches, and the share of that label's rows that would be synthetic
    — the last one because a label lifted from 3 to 100 is 97 % invented, and that
    belongs in front of the user before the run, not in a footnote after it.
    """
    per_label: dict[str, dict] = {}
    skipped: list[str] = []

    rows_by_label = _rows_by_label(df, label_column, label_separator)
    for label, rows in sorted(rows_by_label.items(), key=lambda kv: len(kv[1])):
        support = len(rows)
        deficit = max(0, target_per_label - support)
        if deficit and not any(_has_text(df.iloc[position], fields) for position in rows):
            # Nothing to imitate. Inventing rows from the bare label name would
            # produce exactly the generic filler this feature exists to avoid.
            skipped.append(label)
            continue
        per_label[label] = {
            "support": support,
            "deficit": deficit,
            "batches": math.ceil(deficit / batch_size),
            "synthetic_share": round(deficit / (support + deficit), 3) if deficit else 0.0,
        }

    return {
        "target": target_per_label,
        "rows_to_add": sum(entry["deficit"] for entry in per_label.values()),
        "batches": sum(entry["batches"] for entry in per_label.values()),
        "labels_below_target": sum(1 for entry in per_label.values() if entry["deficit"]),
        "skipped_without_examples": sorted(skipped),
        "per_label": per_label,
    }


# The provenance column. Read by ``holdout_split`` to keep generated text out of an
# evaluation, and by the UI to show how much of a dataset is synthetic.
GENERATED_FOR = "generated_for"

# After the planned batches, this many more attempts to replace what the gates
# rejected. Without a cap a model that keeps repeating itself would be paid for
# indefinitely; with one, the shortfall is reported instead.
_EXTRA_BATCHES = 2


def _fingerprint(cells: list[str]) -> str:
    return "\n".join(cells).lower()


def _examples_for(
    df: pd.DataFrame, positions: list[int], fields: list[TextField], k: int
) -> list[dict]:
    """Up to ``k`` rows for one label, richest first and without repeats.

    Longest combined text first: a one-word title teaches the model nothing about
    what a full entry of this label looks like.
    """
    chosen: dict[str, dict] = {}
    ordered = sorted(
        positions,
        key=lambda p: sum(len(str(df.iloc[p].get(f.column) or "")) for f in fields),
        reverse=True,
    )
    for position in ordered:
        row = {f.column: df.iloc[position].get(f.column) for f in fields}
        key = _fingerprint([str(row.get(f.column) or "") for f in fields])
        chosen.setdefault(key, row)
        if len(chosen) == k:
            break
    return list(chosen.values())


def _accept(item: BalanceItem, fields: list[TextField], seen: set[str]) -> list[str]:
    """The generated cells, or the reason as a ``LookupError``.

    Two gates, both field-generic: every field must carry what it asks for
    (``min_values`` — the dataset's own definition of a gap), and the item must not
    repeat something already there. The PII scrub runs on every value as defense in
    depth on top of the prompt's ban.
    """
    cells = []
    for index, field in enumerate(fields):
        raw = item.values[index] if index < len(item.values) else ""
        values = [scrub(value)[0] for value in read_values(raw, field)]
        if len(values) < field.min_values:
            raise LookupError("discarded_incomplete")
        cells.append(write_values(values, field))
    key = _fingerprint(cells)
    if key in seen:
        raise LookupError("discarded_duplicate")
    seen.add(key)
    return cells


async def _generate_for_label(
    label: str,
    examples: list[dict],
    *,
    fields: list[TextField],
    wanted: int,
    avoid: list[str],
    seen: set[str],
    batch_size: int,
    complete: Complete,
) -> tuple[list[list[str]], dict]:
    """Ask for ``wanted`` accepted items, retrying the shortfall within the budget."""
    accepted: list[list[str]] = []
    counters = {"discarded_duplicate": 0, "discarded_incomplete": 0}
    attempts = math.ceil(wanted / batch_size) + _EXTRA_BATCHES

    while len(accepted) < wanted and attempts:
        attempts -= 1
        missing = wanted - len(accepted)
        prompt = build_balance_prompt(
            label, examples, fields, n=min(missing, batch_size), avoid_titles=avoid)
        batch = await complete(prompt, BalanceBatch)
        for item in batch.items:  # type: ignore[attr-defined]
            if len(accepted) >= wanted:
                break
            try:
                cells = _accept(item, fields, seen)
            except LookupError as reason:
                counters[str(reason)] += 1
                continue
            accepted.append(cells)
            avoid.append(cells[0])
    return accepted, counters


def _refuse_foreign_provenance(df: pd.DataFrame, labels: set[str]) -> None:
    """A ``generated_for`` column holding anything but labels is somebody else's.

    Writing provenance into it would make ``holdout_split`` drop REAL rows from the
    evaluation — silently, and visible only as a metric that is somehow too good.
    """
    if GENERATED_FOR not in df.columns:
        return
    foreign = {str(cell) for cell in df[GENERATED_FOR] if str(cell or "").strip()} - labels
    if foreign:
        raise ValueError(
            f"Column {GENERATED_FOR!r} already holds values that are not labels of this "
            f"dataset (e.g. {sorted(foreign)[0]!r}). Rename it before balancing."
        )


async def balance_dataset(
    df: pd.DataFrame,
    *,
    fields: list[TextField],
    label_column: str,
    target_per_label: int,
    complete: Complete,
    label_separator: str = ",",
    examples_per_label: int = 4,
    batch_size: int = 10,
    limit: int = 500,
) -> tuple[pd.DataFrame, dict]:
    """Generate the rows each short label is missing; returns the new frame and stats.

    The source frame is never modified — the caller saves the result as a NEW dataset.
    Raises ``ValueError`` when the frame already carries a ``generated_for`` column
    holding something other than labels of this dataset.
    """
    plan = plan_balance(df, fields=fields, label_column=label_column,
                        target_per_label=target_per_label,
                        label_separator=label_separator, batch_size=batch_size)
    rows_by_label = _rows_by_label(df, label_column, label_separator)
    _refuse_foreign_provenance(df, set(rows_by_label))

    new = df.copy()
    for column in (GENERATED_FOR, "enriched_fields"):
        new[column] = new[column].fillna("") if column in new.columns else ""

    seen = {_fingerprint([str(new.iloc[p].get(f.column) or "") for f in fields])
            for p in range(len(new))}
    generated: list[dict] = []
    per_label: dict[str, dict] = {}

    for label, entry in plan["per_label"].items():
        if not entry["deficit"] or len(generated) >= limit:
            continue
        wanted = min(entry["deficit"], limit - len(generated))
        accepted, counters = await _generate_for_label(
            label,
            _examples_for(df, rows_by_label[label], fields, examples_per_label),
            fields=fields, wanted=wanted,
            avoid=[str(df.iloc[p].get(fields[0].column) or "") for p in rows_by_label[label]],
            seen=seen, batch_size=batch_size, complete=complete,
        )
        generated.extend(
            {**{f.column: cell for f, cell in zip(fields, cells, strict=True)},
             label_column: label, GENERATED_FOR: label, "enriched_fields": ""}
            for cells in accepted
        )
        per_label[label] = {"support": entry["support"], "added": len(accepted),
                            "missing": wanted - len(accepted), **counters}

    if generated:
        new = pd.concat([new, pd.DataFrame(generated)], ignore_index=True).fillna("")
    return new, {
        "target": target_per_label,
        "rows_added": len(generated),
        "labels_filled": sum(1 for e in per_label.values() if e["added"] and not e["missing"]),
        "skipped_without_examples": plan["skipped_without_examples"],
        "per_label": per_label,
    }
