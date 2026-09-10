"""Row filtering by column value: a list of rules, combined with AND or OR.

Sibling to ``filters.py`` rather than part of it. Those filters all read the
combined text or the label column -- the training-data layer. A rule here reads
an arbitrary cell, so it works on an export before anyone has decided which
column is the label.

A list of rules rather than an expression language, deliberately: an expression
needs a parser and its own error messages, and ``pandas.query`` -- the obvious
shortcut -- evaluates Python and would turn a filter box into code execution.

**How a comparison is decided.** The store is all-strings, so ``"9" > "10"``
would be true as text. Rather than ask operators to declare column types, the
rule's own VALUE decides: a JSON number compares numerically, a string compares
as text. So ``jahr >= 2015`` does arithmetic while ``datum >= "2026-01-01"``
does the lexicographic comparison an ISO date wants. Cells that cannot be read
as numbers never match a numeric rule, and their count is reported rather than
swallowed.
"""

from __future__ import annotations

import operator
import re
from typing import TypeGuard

import pandas as pd

OPERATORS = (
    "eq", "ne", "lt", "lte", "gt", "gte", "between", "in", "not_in",
    "contains", "starts_with", "ends_with", "regex", "is_empty", "not_empty",
)

_ORDERED = {"lt", "lte", "gt", "gte", "between"}
_TEXTUAL = {"contains", "starts_with", "ends_with", "regex"}
_COMPARE = {"lt": operator.lt, "lte": operator.le, "gt": operator.gt, "gte": operator.ge}

# A pattern is caller-controlled input and Python's `re` has no timeout, so an
# unbounded one can backtrack catastrophically and burn a core. Length is a
# blunt bound, but it is the one that costs nothing to check.
_MAX_PATTERN = 200

_EXAMPLES = 5


def _is_number(value: object) -> TypeGuard[int | float]:
    """A JSON number -- not a numeric-looking string.

    The distinction is the whole contract: an id of "007" must not be read as
    the number 7 just because it could be.
    """
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _numeric(series: pd.Series) -> pd.Series:
    return pd.to_numeric(series, errors="coerce")


def _text(series: pd.Series, case_sensitive: bool) -> pd.Series:
    text = series.fillna("").astype(str)
    return text if case_sensitive else text.str.lower()


def _compare(column: pd.Series, rule: dict, op: str) -> pd.Series:
    value = rule.get("value")
    if op == "between":
        if "value2" not in rule or rule["value2"] is None:
            raise ValueError("Operator 'between' needs a second bound in 'value2'.")
        lower, upper = value, rule["value2"]
        if _is_number(lower) and _is_number(upper):
            numbers = _numeric(column)
            return (numbers >= lower) & (numbers <= upper)
        text = column.fillna("").astype(str)
        return (text >= str(lower)) & (text <= str(upper))

    # One vectorised pass over the column; a dict of all four comparisons
    # evaluated every one of them eagerly and then picked one.
    if _is_number(value):
        return _COMPARE[op](_numeric(column), value)
    return _COMPARE[op](column.fillna("").astype(str), str(value))


def _match(column: pd.Series, rule: dict) -> pd.Series:
    op = rule.get("op")
    if op not in OPERATORS:
        raise ValueError(f"Unknown operator {op!r}. Available: {', '.join(OPERATORS)}.")
    value = rule.get("value")
    case_sensitive = bool(rule.get("case_sensitive", False))

    if op == "is_empty":
        return column.fillna("").astype(str).str.strip() == ""
    if op == "not_empty":
        return column.fillna("").astype(str).str.strip() != ""

    if op in ("eq", "ne"):
        if _is_number(value):
            hit = _numeric(column) == value
        else:
            hit = column.fillna("").astype(str) == str(value)
        return ~hit if op == "ne" else hit

    if op in ("in", "not_in"):
        if not isinstance(value, (list, tuple, set)):
            raise ValueError(f"Operator {op!r} needs a list in 'value'.")
        wanted = {str(item) for item in value}
        hit = column.fillna("").astype(str).isin(wanted)
        return ~hit if op == "not_in" else hit

    if op in _ORDERED:
        return _compare(column, rule, op)

    if op in _TEXTUAL:
        needle = str(value)
        text = _text(column, case_sensitive)
        if op == "regex":
            if len(needle) > _MAX_PATTERN:
                raise ValueError(
                    f"Regular expression too long ({len(needle)} characters, "
                    f"maximum {_MAX_PATTERN})."
                )
            try:
                re.compile(needle)
            except re.error as exc:
                raise ValueError(f"Not a valid regular expression: {exc}.") from exc
            return text.str.contains(needle, regex=True, na=False,
                                     case=case_sensitive)
        if not case_sensitive:
            needle = needle.lower()
        if op == "contains":
            return text.str.contains(needle, regex=False, na=False)
        if op == "starts_with":
            return text.str.startswith(needle, na=False)
        return text.str.endswith(needle, na=False)

    raise ValueError(f"Operator {op!r} is declared but not implemented.")  # pragma: no cover


def evaluate(df: pd.DataFrame, rules: list[dict], *, combine: str = "and") -> pd.Series:
    """A boolean mask over ``df`` for ``rules`` joined by ``combine``.

    Raises ``ValueError`` (client-safe) for an unknown column, operator or
    combine mode, and for a rule missing what its operator needs.
    """
    if combine not in ("and", "or"):
        raise ValueError(f"Unknown combine mode {combine!r}. Use 'and' or 'or'.")
    if not rules:
        # An empty rule list would keep every row while looking like a filter.
        raise ValueError("Provide at least one rule.")

    masks = []
    for rule in rules:
        column = rule.get("column")
        if column not in df.columns:
            raise ValueError(
                f"Unknown column {column!r}. Available: {', '.join(map(str, df.columns))}."
            )
        masks.append(_match(df[column], rule).fillna(False).astype(bool))

    mask = masks[0]
    for other in masks[1:]:
        mask = (mask & other) if combine == "and" else (mask | other)
    return mask


def _not_comparable(df: pd.DataFrame, rules: list[dict]) -> int:
    """Rows a numeric rule could not read as numbers.

    Reported rather than swallowed: it is how an operator notices they pointed
    a numeric rule at a text column.
    """
    columns = {
        rule["column"] for rule in rules
        if rule.get("op") in _ORDERED and _is_number(rule.get("value"))
    }
    if not columns:
        return 0
    unreadable = pd.Series(False, index=df.index)
    for column in columns:
        unreadable |= _numeric(df[column]).isna()
    return int(unreadable.sum())


def filter_rows(df: pd.DataFrame, params: dict, ctx: dict) -> tuple[pd.DataFrame, dict]:
    """Pipeline operation: keep the rows matching ``params['rules']``.

    Same shape as every filter in ``filters.py`` -- ``(df, params, ctx) ->
    (new_df, stats)``, never mutating the input -- so both kinds of step share
    one operation history. ``ctx`` is unused: that is the point of this module.
    """
    rules = params.get("rules") or []
    mask = evaluate(df, rules, combine=params.get("combine", "and"))
    new = df[mask].reset_index(drop=True)
    removed = df[~mask]
    return new, {
        "filter": "rules",
        "before": int(len(df)),
        "after": int(len(new)),
        "removed": int(len(df) - len(new)),
        "changed": 0,
        "not_comparable": _not_comparable(df, rules),
        "examples": [
            {"removed": " | ".join(map(str, row))[:120]}
            for row in removed.head(_EXAMPLES).itertuples(index=False)
        ],
    }
