"""Insufficient history detection."""

from __future__ import annotations

from cauterule.models.trajectory import Trajectory


def check_history(trajectories: list[Trajectory], min_count: int = 3) -> tuple[bool, str]:
    """Check if history is sufficient for replay.

    Args:
        trajectories: Historical trajectories.
        min_count: Minimum required (default 3).

    Returns:
        (is_sufficient, message) where insufficient yields inconclusive verdict.
    """
    if len(trajectories) < min_count:
        return False, f"insufficient history: {len(trajectories)} < {min_count} trajectories"
    return True, "ok"


def history_verdict(trajectories: list[Trajectory]) -> str:
    """Return verdict override based on history size."""
    sufficient, _ = check_history(trajectories)
    return "inconclusive" if not sufficient else "ok"
