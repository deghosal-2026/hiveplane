"""Replay diff — before/after comparison of agent behavior with and without rule."""

from __future__ import annotations

from typing import Any

from cauterule.models.candidate import CandidateRule
from cauterule.models.trajectory import Trajectory
from cauterule.replay.trace import build_trace


def diff(candidate: CandidateRule, trajectory: Trajectory) -> dict[str, Any]:
    """Produce before/after diff for *candidate* on *trajectory*.

    Returns a dict with before (original outcome) and after (simulated outcome).
    """
    before = "failure" if not trajectory.success else "success"
    trace = build_trace(candidate, trajectory)
    after = trace["outcome"]
    return {
        "trajectory_id": trajectory.id,
        "before": before,
        "after": after,
        "changed": after in ("prevented", "broken"),
        "trigger": candidate.when.trigger,
        "directive": candidate.do.directive,
    }
