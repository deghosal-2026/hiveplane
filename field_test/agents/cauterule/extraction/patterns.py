"""Cross-failure pattern detection."""

from __future__ import annotations

from collections import Counter

from cauterule.models.candidate import CandidateRule
from cauterule.models.rule import RuleDo, RuleWhen
from cauterule.models.trajectory import Trajectory


def detect_repeated_failures(
    trajectories: list[Trajectory],
    min_count: int = 3,
) -> list[tuple[str, int]]:
    """Detect repeated failure classes.

    Args:
        trajectories: List of trajectories (typically failures).
        min_count: Minimum occurrences to be considered repeated.

    Returns:
        List of (failure_class, count) for repeated classes, sorted by count desc.
    """
    counts = Counter(t.failure_class for t in trajectories if t.failure_class)
    repeated = [(cls, cnt) for cls, cnt in counts.items() if cnt >= min_count]
    repeated.sort(key=lambda x: x[1], reverse=True)
    return repeated


def extract_stronger_rule(
    failure_class: str,
    trajectories: list[Trajectory],
    count: int,
) -> CandidateRule:
    """Extract a stronger rule for a repeated failure class.

    Args:
        failure_class: Failure class that repeats.
        trajectories: All trajectories for context.
        count: Number of occurrences.

    Returns:
        Candidate rule addressing the pattern.
    """
    # Example: if git/push fails 3 times, suggest a stronger check.
    trigger = f"when {failure_class} fails repeatedly ({count} times)"
    directive = f"Verify preconditions for {failure_class} before acting and add automated checks"
    return CandidateRule(
        when=RuleWhen(trigger=trigger),
        do=RuleDo(directive=directive, because=f"Seen {count} failures of same class"),
        confidence=0.85,
        reasoning=f"Repeated failure pattern: {failure_class} x{count}",
        extraction_pass=1,
        template="check-preconditions",
    )
