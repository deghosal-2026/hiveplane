"""Preflight mode — predict likely failures and recommend rules before task."""

from __future__ import annotations

from cauterule.injection.matcher import match_rules
from cauterule.injection.ordering import order_by_specificity
from cauterule.models.rule import StandingRule


def preflight(task: str, rules: list[StandingRule]) -> list[StandingRule]:
    """Predict likely failures for *task* and recommend matching rules.

    Performs a broad match (trigger + tag + taxonomy) and returns the
    most specific matching rules.  Unlike ``match_rules``, preflight does
    not require error or tool context — it operates on the task alone.

    Args:
        task: Task description to analyze.
        rules: List of standing rules to consider.

    Returns:
        Matching rules ordered by specificity (most specific first).
        Empty list if no rules match.
    """
    matched = match_rules(task, rules)
    return order_by_specificity(matched)
