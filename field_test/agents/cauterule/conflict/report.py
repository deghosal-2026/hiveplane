"""Conflict report builder — convenience for constructing ConflictReport instances."""

from __future__ import annotations

from cauterule.models.conflict import ConflictReport


def build_contradiction_report(
    rule_ids: tuple[str, ...],
    trigger: str,
    resolution: str | None = None,
) -> ConflictReport:
    """Build a contradiction ConflictReport."""
    return ConflictReport(
        type="contradiction",
        rules=rule_ids,
        trigger=trigger,
        resolution=resolution,
    )


def build_duplicate_report(
    rule_ids: tuple[str, ...],
    trigger: str | None = None,
    resolution: str | None = None,
) -> ConflictReport:
    """Build a duplicate ConflictReport."""
    return ConflictReport(
        type="duplicate",
        rules=rule_ids,
        trigger=trigger,
        resolution=resolution,
    )


def build_overlap_report(
    rule_ids: tuple[str, ...],
    trigger: str | None = None,
    resolution: str | None = None,
) -> ConflictReport:
    """Build an overlap ConflictReport."""
    return ConflictReport(
        type="overlap",
        rules=rule_ids,
        trigger=trigger,
        resolution=resolution,
    )


def build_specificity_report(
    rule_ids: tuple[str, ...],
    scores: dict[str, float],
    resolution: str | None = None,
) -> ConflictReport:
    """Build a specificity ConflictReport."""
    return ConflictReport(
        type="specificity",
        rules=rule_ids,
        specificity_scores=scores,
        resolution=resolution,
    )
