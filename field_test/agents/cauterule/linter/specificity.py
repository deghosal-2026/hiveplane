"""Specificity linter — flags over-broad triggers."""

from __future__ import annotations

from cauterule.extraction.specificity import is_generic, score_specificity


def check_specificity(trigger: str) -> list[str]:
    """Return warning if *trigger* is generic or too broad."""
    warnings: list[str] = []
    if is_generic(trigger):
        warnings.append(f"generic trigger: '{trigger.strip()}' is too broad to verify")
    return warnings


def check_broadness(trigger: str) -> list[str]:
    """Return warning if *trigger* is structurally too broad.

    A trigger is structurally too broad if:
    - It has ≤3 content tokens AND doesn't name a concrete tool
    - It contains only generic failure words (fail, error, issue, problem, etc.)
    """
    spec = score_specificity(trigger)
    if spec == "generic":
        return [f"broad trigger: '{trigger.strip()}' — too generic, may over-fire"]
    return []
