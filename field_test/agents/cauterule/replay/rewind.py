"""Failure Time Machine — step-by-step replay with rule overlay."""

from __future__ import annotations

from typing import Any

from cauterule.models.candidate import CandidateRule
from cauterule.models.trajectory import Trajectory
from cauterule.replay.matcher import rule_matches


def rewind(
    trajectory: Trajectory,
    candidate: CandidateRule,
) -> dict[str, Any]:
    """Replay a trajectory step by step with rule overlay.

    Shows each step and whether the rule would have changed the outcome.

    Args:
        trajectory: Original trajectory.
        candidate: Candidate rule to overlay.

    Returns:
        Dict with trajectory_id, matched, step_by_step, original_outcome, simulated_outcome.
    """
    matched = rule_matches(candidate, trajectory)
    steps: list[dict[str, object]] = []
    for step in trajectory.steps:
        step_info: dict[str, object] = {
            "step_number": step.step_number,
            "tool": step.tool,
            "input": step.input or "",
            "output": step.output or "",
            "error": step.error or "",
        }
        # If rule matches, suggest directive at the failing step
        if matched and not trajectory.success and step.error:
            step_info["rule_would_apply"] = True
            step_info["suggested_action"] = candidate.do.directive
        else:
            step_info["rule_would_apply"] = False
        steps.append(step_info)

    return {
        "trajectory_id": trajectory.id,
        "task": trajectory.task,
        "original_success": trajectory.success,
        "matched": matched,
        "simulated_success": True if matched and not trajectory.success else trajectory.success,
        "step_by_step": steps,
        "trigger": candidate.when.trigger,
        "directive": candidate.do.directive,
    }
