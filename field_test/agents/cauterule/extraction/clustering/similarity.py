"""Similarity scorer for failure clustering."""

from __future__ import annotations

from cauterule.models.trajectory import Trajectory


def _jaccard(a: set[str], b: set[str]) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    inter = len(a & b)
    union = len(a | b)
    return inter / union if union else 0.0


def _error_overlap(a: str | None, b: str | None) -> float:
    if not a and not b:
        return 1.0
    if not a or not b:
        return 0.0
    # Simple token overlap.
    tokens_a = set(a.lower().split())
    tokens_b = set(b.lower().split())
    return _jaccard(tokens_a, tokens_b)


def similarity(a: Trajectory, b: Trajectory) -> float:
    """Compute similarity between two trajectories (0.0-1.0).

    Weighted: failure_class (0.5) + tool sequence (0.3) + error message (0.2).

    Args:
        a: First trajectory.
        b: Second trajectory.
    """
    # Failure class: exact match 1.0, same prefix 0.7, else 0.0
    if a.failure_class and b.failure_class:
        if a.failure_class == b.failure_class:
            class_score = 1.0
        elif a.failure_class.split("/")[0] == b.failure_class.split("/")[0]:
            class_score = 0.7
        else:
            class_score = 0.0
    elif not a.failure_class and not b.failure_class:
        class_score = 1.0
    else:
        class_score = 0.0

    # Tool sequence Jaccard
    tools_a = {s.tool for s in a.steps}
    tools_b = {s.tool for s in b.steps}
    tool_score = _jaccard(tools_a, tools_b)

    # Error overlap: compare first error
    err_a = next((s.error for s in a.steps if s.error), None)
    err_b = next((s.error for s in b.steps if s.error), None)
    error_score = _error_overlap(err_a, err_b)

    return 0.5 * class_score + 0.3 * tool_score + 0.2 * error_score
