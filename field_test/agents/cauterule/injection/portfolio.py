"""Lesson portfolio optimizer — select N rules maximizing expected prevention."""

from __future__ import annotations

from cauterule.models.rule import StandingRule


def _expected_prevention(rule: StandingRule) -> float:
    score = rule.confidence
    score += min(len(rule.when.trigger.split()) * 0.05, 0.3)
    score += len(rule.when.context) * 0.1
    if rule.do.because:
        score += 0.1
    return score


def optimize_portfolio(rules: list[StandingRule], max_rules: int) -> list[StandingRule]:
    """Select up to *max_rules* rules that maximize expected failure prevention.

    Uses a greedy selection algorithm: repeatedly picks the highest-value
    rule that has not yet been selected.

    Args:
        rules: List of standing rules to consider.
        max_rules: Maximum number of rules to return.

    Returns:
        Up to *max_rules* rules, ordered by estimated prevention value
        (highest first).
    """
    scored = [(r, _expected_prevention(r)) for r in rules]
    scored.sort(key=lambda x: x[1], reverse=True)
    return [r for r, _ in scored[:max_rules]]
