"""What a run has spent, and the caps that stop it.

Split from ``llm.py``, which was past the ~300-line rule the constitution sets.
The seam is real rather than arithmetic: this module answers "how much has this
cost and are we over budget", while ``llm.py`` answers "talk to the model". A
change to one is almost never a change to the other.

Two levels of cap. ``Budgets.max_llm_calls`` / ``max_tokens_total`` bound ONE
run. The ``SpendLedger`` adds an optional process-wide ceiling across every run
and request -- a circuit breaker against a leaked key or a runaway retry loop.
It is in-memory and therefore per-process, which the single-worker design makes
sufficient.
"""

from __future__ import annotations

from dataclasses import dataclass

from .config import Budgets
from .llm_errors import BudgetExceeded


@dataclass
class Usage:
    """Accumulated logical calls and tokens (SDK-internal retries count once)."""

    calls: int = 0
    tokens_total: int = 0

    def as_dict(self) -> dict:
        return {"calls": self.calls, "tokens_total": self.tokens_total}


@dataclass
class SpendLedger:
    """Process-local cross-request spend accumulator (single-worker design).

    Per-run :class:`Usage`/:class:`Budgets` bound one request; this bounds TOTAL
    calls/tokens across every request and background run in the process — a
    circuit breaker against a leaked key or a runaway retry loop. Caps of 0 mean
    "no ceiling" (disabled), so it changes nothing unless an operator opts in.
    """

    calls: int = 0
    tokens: int = 0
    max_calls: int = 0
    max_tokens: int = 0

    def check(self) -> None:
        """Raise :class:`BudgetExceeded` if the NEXT call would cross a cap."""
        if self.max_calls and self.calls + 1 > self.max_calls:
            raise BudgetExceeded(
                f"Cross-request budget reached: {self.calls} LLM calls (cap {self.max_calls})."
            )
        if self.max_tokens and self.tokens >= self.max_tokens:
            raise BudgetExceeded(
                f"Cross-request budget reached: {self.tokens} tokens (cap {self.max_tokens})."
            )

    def count_call(self) -> None:
        self.calls += 1

    def count_tokens(self, n: int) -> None:
        self.tokens += n


_ledger = SpendLedger()


def process_ledger(budgets: Budgets | None = None) -> SpendLedger:
    """The one process-local ledger; (re)applies the configured caps when given."""
    if budgets is not None:
        _ledger.max_calls = budgets.max_cross_request_calls
        _ledger.max_tokens = budgets.max_cross_request_tokens
    return _ledger


def reset_ledger() -> None:
    """Test hook: clear counters and caps so cases do not leak into each other."""
    _ledger.calls = _ledger.tokens = 0
    _ledger.max_calls = _ledger.max_tokens = 0
