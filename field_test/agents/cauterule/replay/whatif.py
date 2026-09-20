"""What-if? mode — apply a hypothetical rule and simulate the outcome."""

from __future__ import annotations

from typing import Any

from cauterule.models.candidate import CandidateRule
from cauterule.models.rule import RuleDo, RuleWhen
from cauterule.models.trajectory import Trajectory
from cauterule.replay.report import build_evidence_report


def what_if(
    task: str,
    trigger: str,
    directive: str,
    trajectories: list[Trajectory],
    context: tuple[str, ...] = (),
) -> dict[str, Any]:
    """Apply a hypothetical (user-written) rule and simulate outcome.

    Args:
        task: Human-readable description of the hypothetical rule.
        trigger: ``when`` trigger.
        directive: ``do`` directive.
        trajectories: Historical trajectories to test against.
        context: Optional context items.

    Returns:
        Dict with report, task, and hypothetical rule details.
    """
    candidate = CandidateRule(
        when=RuleWhen(trigger=trigger, context=context),
        do=RuleDo(directive=directive),
        confidence=0.85,
        reasoning=f"what-if: {task}",
    )
    report = build_evidence_report(candidate, trajectories)
    return {
        "task": task,
        "trigger": trigger,
        "directive": directive,
        "context": list(context),
        "failures_prevented": list(report.failures_prevented),
        "successes_broken": list(report.successes_broken),
        "near_misses": list(report.near_misses),
        "precision": report.precision,
        "recall": report.recall,
        "verdict": report.verdict,
    }
