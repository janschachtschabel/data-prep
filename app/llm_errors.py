"""The LLM layer's error types, in one place both halves can import.

Their own module because ``llm.py`` (talk to the model) and ``llm_budget.py``
(what it costs) both raise them, and either importing the other would be a
cycle.

The hierarchy is load-bearing, not decoration: ``BudgetExceeded`` inherits from
``LlmError`` so that a caller handling "the LLM failed" also handles "we ran out
of budget" if it does not distinguish them. Five call sites catch ``LlmError``;
flattening this would send a budget stop out as a 500.
"""

from __future__ import annotations


class LlmError(Exception):
    """LLM call failed for a non-budget reason; the message is client-safe."""


class BudgetExceeded(LlmError):
    """A run budget cap was reached — pause instead of continuing to spend."""


class LlmConfigError(LlmError):
    """Fatal configuration problem (e.g. a missing API key) — retrying won't help,
    so the caller must stop rather than retry/skip like a transient failure."""

