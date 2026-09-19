"""Asking the model for one label's rows, and gating what it answers.

Separate from ``balance.py`` because this is the part that talks to the model and
decides what counts as a row; the other module reads the frame, plans, and
assembles the result. Both are needed for a run, but they change for different
reasons — a new gate here, a new statistic there.
"""

from __future__ import annotations

import math
import statistics
from collections.abc import Awaitable, Callable, Collection
from dataclasses import dataclass
from dataclasses import field as dc_field

from pydantic import BaseModel

from ..pii import scrub
from .balance_prompt import (
    BalanceBatch,
    BalanceItem,
    PromptContext,
    build_balance_prompt,
    output_budget,
    shown_cells,
)
from .fields import TextField, read_values, write_values
from .prompt_context import MAX_VALUE_CHARS

# The injected model call. Keyword arguments carry the per-batch output budget.
Complete = Callable[..., Awaitable[BaseModel]]

# The floor catches degenerate answers, not brief ones: a quarter of the shortest
# example as shown, never more than this.
_FLOOR_CAP = 40

# After the planned batches, this many more attempts per label to replace what the
# gates rejected. Without a cap a model that keeps repeating itself would be paid
# for indefinitely; with one, the shortfall is reported instead.
EXTRA_BATCHES = 2


def _no_discards() -> dict[str, int]:
    return {"discarded_duplicate": 0, "discarded_incomplete": 0, "discarded_short": 0,
            "discarded_long": 0}


@dataclass
class LabelResult:
    """What generating for one label produced."""

    accepted: list[list[str]] = dc_field(default_factory=list)
    counters: dict[str, int] = dc_field(default_factory=_no_discards)
    # Whether any call went out — only then were the examples shown to the model.
    sent: bool = False
    # Why generation ended early, when a stop signal did end it.
    stopped: str | None = None


class _Rejected(Exception):  # noqa: N818 - a verdict, not an error condition
    """A generated item that does not become a row, and why.

    Its own class: rejections used to travel as LookupError, and KeyError is one —
    a genuine bug inside a gate was counted as a discarded item.
    """

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


def fingerprint(cells: list[str]) -> str:
    """Identity of a row for the duplicate gate — over NORMALISED cells, so "a,b"
    stored by an import and "a, b" written by the generator are the same list."""
    return "\n".join(cells).casefold()


def pick_examples(
    positions: list[int], cells: list[list[str]], k: int, touched: Collection[int] = (),
) -> list[int]:
    """Up to ``k`` of ``positions``: complete rows first, rows an LLM completed
    (``touched``) after the untouched ones, and within each the rows closest to the
    median length of the label's complete rows; no row twice.

    A row missing a field teaches the model nothing about that field, and a completed
    row shows it the LLM's own words as a "real entry". Longest-first -- the rule before
    -- showed a label's richest rows, and the generated rows came out longer and richer
    than its real ones: a difference a classifier learns as a feature of the label. The
    median is the complete rows' own: counted in, the shorter incomplete ones would pull
    the choice toward the short end. Ties go to the longer row.
    """
    if not positions:
        return []
    size = {p: sum(map(len, cells[p])) for p in positions}
    complete = [p for p in positions if all(cells[p])]
    median = statistics.median(size[p] for p in (complete or positions))
    chosen: dict[str, int] = {}
    for position in sorted(positions, key=lambda p: (not all(cells[p]), p in touched,
                                                     abs(size[p] - median), -size[p])):
        chosen.setdefault(fingerprint(cells[position]), position)
        if len(chosen) == k:
            break
    return list(chosen.values())


def _min_chars(examples: list[dict], fields: list[TextField]) -> list[int]:
    """Per field, the shortest acceptable single value.

    Fields have no common length — a title is not a description — so the bar comes
    from this label's examples AS SHOWN: computed from full cells, a long example
    set a floor above everything the prompt displayed, and every answer that followed
    the prompt was discarded after being paid for. List fields are gated by
    ``min_values`` instead.
    """
    shown = [shown_cells(row, fields) for row in examples]
    floors = []
    for index, field in enumerate(fields):
        lengths = [len(cells[index]) for cells in shown if cells[index]]
        floors.append(0 if field.separator or not lengths
                      else min(min(lengths) // 4, _FLOOR_CAP))
    return floors


def _parts(raw: str, field: TextField) -> list[str]:
    """A cell's values as the gate sees them: scrubbed WHOLE, then split — split
    first, "+49 30 1234567" is three harmless numbers."""
    return read_values(scrub(raw)[0], field)


def _seed(seen: set[str], examples: list[dict], fields: list[TextField]) -> None:
    """Teach the duplicate gate what the model was shown, in the form a copy of it
    would arrive in: cut to the prompt's length, and scrubbed like every answer."""
    for row in examples:
        cells = shown_cells(row, fields)
        seen.add(fingerprint(cells))
        seen.add(fingerprint([write_values(_parts(cell, f), f)
                              for cell, f in zip(cells, fields, strict=True)]))


def _accept(item: BalanceItem, fields: list[TextField], seen: set[str],
            min_chars: list[int]) -> list[str]:
    """The generated cells, or :class:`_Rejected` with the reason.

    Completeness first — every field must carry its ``min_values``, the dataset's
    own definition of a gap — then length, then repetition. Too long is a cell above
    ``MAX_VALUE_CHARS``, the most an answer value may hold.
    """
    values = []
    for index, field in enumerate(fields):
        raw = item.values[index] if index < len(item.values) else ""
        parts = _parts(raw, field)
        if len(parts) < field.min_values:
            raise _Rejected("discarded_incomplete")
        values.append(parts)
    cells = [write_values(parts, field) for parts, field in zip(values, fields, strict=True)]
    if any(len(cell) < floor for cell, floor in zip(cells, min_chars, strict=True)):
        raise _Rejected("discarded_short")
    if any(len(cell) > MAX_VALUE_CHARS for cell in cells):
        raise _Rejected("discarded_long")
    key = fingerprint(cells)
    if key in seen:
        raise _Rejected("discarded_duplicate")
    seen.add(key)
    return cells


async def generate_for_label(
    label: str,
    examples: list[dict],
    *,
    fields: list[TextField],
    wanted: int,
    avoid: list[str],
    seen: set[str],
    batch_size: int,
    complete: Complete,
    stop_on: tuple[type[BaseException], ...] = (),
    context: PromptContext | None = None,
) -> LabelResult:
    """Ask for ``wanted`` accepted items, retrying the shortfall within the budget.

    ``avoid`` and ``seen`` are extended with what is accepted, so later batches —
    and later labels — do not repeat it. An exception in ``stop_on`` ends the label
    early instead of failing it: what was accepted so far was paid for and is
    returned, with the reason in ``stopped``.
    """
    result = LabelResult()
    floors = _min_chars(examples, fields)
    _seed(seen, examples, fields)
    attempts = math.ceil(wanted / batch_size) + EXTRA_BATCHES

    while len(result.accepted) < wanted and attempts:
        attempts -= 1
        n = min(wanted - len(result.accepted), batch_size)
        prompt = build_balance_prompt(label, examples, fields, n=n, avoid_titles=avoid,
                                      context=context)
        try:
            batch = await complete(prompt, BalanceBatch,
                                   max_output_tokens=output_budget(examples, fields, n, context))
        except stop_on as signal:
            result.stopped = str(signal) or signal.__class__.__name__
            break
        result.sent = True
        for item in batch.items:  # type: ignore[attr-defined]
            if len(result.accepted) >= wanted:
                break
            try:
                cells = _accept(item, fields, seen, floors)
            except _Rejected as rejection:
                result.counters[rejection.reason] += 1
                continue
            result.accepted.append(cells)
            avoid.append(cells[0])
    return result
