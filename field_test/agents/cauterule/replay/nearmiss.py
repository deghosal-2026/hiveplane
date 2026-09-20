"""Near-miss logger — logs partial matches as near misses."""

from __future__ import annotations

from typing import Any

from cauterule.models.candidate import CandidateRule
from cauterule.models.trajectory import Trajectory
from cauterule.replay.matcher import is_near_miss


def log_near_misses(
    candidate: CandidateRule, trajectories: list[Trajectory]
) -> list[dict[str, Any]]:
    """Log near-miss entries for *candidate* against *trajectories*.

    Near misses are trajectories where trigger matches but not all context items do.
    They are not counted as prevented or broken.
    """
    results: list[dict[str, object]] = []
    for traj in trajectories:
        if is_near_miss(candidate, traj):
            results.append(
                {
                    "trajectory_id": traj.id,
                    "task": traj.task,
                    "success": traj.success,
                    "trigger": candidate.when.trigger,
                    "context": list(candidate.when.context),
                    "reason": "trigger matched but not all context items",
                }
            )
    return results
