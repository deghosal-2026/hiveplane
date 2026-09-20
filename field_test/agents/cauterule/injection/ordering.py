"""Specificity ordering — more specific rules first."""

from __future__ import annotations

from cauterule.models.rule import StandingRule


def _specificity_score(rule: StandingRule) -> int:
    score = 0
    if rule.when.trigger:
        score += len(rule.when.trigger.split()) * 2
    score += len(rule.when.context) * 3
    if rule.tags:
        score += len(rule.tags) * 4
    if rule.taxonomy:
        score += 5
    if rule.do.because:
        score += 2
    score += round(rule.confidence * 10)
    return score


def order_by_specificity(rules: list[StandingRule]) -> list[StandingRule]:
    """Sort *rules* from most specific to least specific.

    Specificity is a heuristic based on trigger length (word count),
    context count, tag count, taxonomy presence, rationale presence,
    and confidence score.

    Args:
        rules: List of standing rules to sort.

    Returns:
        New list sorted descending by specificity.
    """
    return sorted(rules, key=_specificity_score, reverse=True)
