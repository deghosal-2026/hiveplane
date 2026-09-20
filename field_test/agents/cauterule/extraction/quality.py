"""Quality checks for extracted candidates."""

from __future__ import annotations

from cauterule.models.candidate import CandidateRule
from cauterule.models.trajectory import Trajectory


def check_quality(
    candidate: CandidateRule, trajectory: Trajectory, threshold: float = 0.6
) -> list[str]:
    """Validate *candidate* against *trajectory*.

    Checks: valid structure (handled by dataclass), references trajectory,
    not tautological, confidence >= *threshold*.

    Args:
        candidate: Extracted candidate rule.
        trajectory: Source trajectory the candidate was extracted from.
        threshold: Minimum confidence (defaults to
            ``ExtractionConfig.confidence_threshold`` = 0.6, #497).

    Returns:
        List of warning messages (empty if valid).
    """
    warnings: list[str] = []

    # Confidence threshold
    if candidate.confidence < threshold:
        warnings.append(f"confidence {candidate.confidence:.2f} below {threshold:.2f}")

    # References trajectory: trigger or directive should mention a token from failure
    task_tokens = set(trajectory.task.lower().split()) if trajectory.task else set()
    failure_tokens: set[str] = set()
    if trajectory.failure_class:
        failure_tokens.update(trajectory.failure_class.lower().split("/"))
    for step in trajectory.steps:
        if step.error:
            failure_tokens.update(step.error.lower().split())
        if step.tool:
            failure_tokens.add(step.tool.lower())

    trigger_lower = candidate.when.trigger.lower()
    directive_lower = candidate.do.directive.lower()
    combined = f"{trigger_lower} {directive_lower}"
    # If no overlap with trajectory tokens, warn.
    if (task_tokens or failure_tokens) and not (
        any(tok in combined for tok in task_tokens)
        or any(tok in combined for tok in failure_tokens)
    ):
        warnings.append("candidate does not reference trajectory")

    # Not tautological: trigger and directive should not be identical
    if trigger_lower.strip() == directive_lower.strip() and trigger_lower.strip():
        warnings.append("tautological: when and do are identical")

    # Not tautological phrase like "when failing, don't fail"
    if "when fail" in trigger_lower and "don't fail" in directive_lower:
        warnings.append("tautological phrase")

    return warnings


def is_valid(candidate: CandidateRule, trajectory: Trajectory, threshold: float = 0.6) -> bool:
    """Return ``True`` if *candidate* passes quality checks."""
    return len(check_quality(candidate, trajectory, threshold)) == 0
