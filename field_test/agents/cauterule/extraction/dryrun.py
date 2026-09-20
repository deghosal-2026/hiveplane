"""Dry-run mode."""

from __future__ import annotations

from cauterule.extraction.prompt import build_extraction_prompt
from cauterule.models.candidate import CandidateRule
from cauterule.models.rule import RuleDo, RuleWhen
from cauterule.models.trajectory import Trajectory


def dry_run(trajectory: Trajectory, template: str | None = None) -> CandidateRule:
    """Show what would be extracted without an LLM call.

    Returns a :class:`CandidateRule` built from the trajectory metadata.

    Args:
        trajectory: Trajectory that would be extracted from.
        template: Optional template hint.
    """
    prompt = build_extraction_prompt(trajectory, template=template)
    when_trigger = f"when {trajectory.failure_class or trajectory.task[:50]}"
    do_directive = "apply fix based on trajectory"
    return CandidateRule(
        when=RuleWhen(trigger=when_trigger),
        do=RuleDo(directive=do_directive),
        confidence=0.0,
        reasoning=f"Dry-run mode — no LLM call. Prompt was:\n{prompt}",
        template=template,
    )
