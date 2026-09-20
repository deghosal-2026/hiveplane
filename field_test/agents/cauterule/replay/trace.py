"""Replay trace builder — step-by-step trace of how a rule changes each trajectory."""

from __future__ import annotations

from typing import Any

from cauterule.models.candidate import CandidateRule
from cauterule.models.trajectory import Trajectory
from cauterule.replay.simulator import simulate


def build_trace(candidate: CandidateRule, trajectory: Trajectory) -> dict[str, Any]:
    """Build a step-by-step trace for *candidate* applied to *trajectory*.

    Returns a dict with trajectory_id, outcome, matched, and step details.
    """
    outcome = simulate(candidate, trajectory)
    return {
        "trajectory_id": trajectory.id,
        "task": trajectory.task,
        "success": trajectory.success,
        "outcome": outcome,
        "trigger": candidate.when.trigger,
        "directive": candidate.do.directive,
        "step_count": len(trajectory.steps),
    }
