"""Human correction capture."""

from __future__ import annotations

import re

from cauterule.models.candidate import CandidateRule
from cauterule.models.rule import RuleDo, RuleWhen
from cauterule.models.trajectory import Trajectory

_CORRECTION_PATTERNS = [
    re.compile(r"next time (?:do|try|use)\s+(.+)", re.IGNORECASE),
    re.compile(r"should have\s+(.+)", re.IGNORECASE),
    re.compile(r"please\s+(.+)", re.IGNORECASE),
]

DEFAULT_CORRECTION_CONFIDENCE: float = 0.8


def parse_correction(
    text: str, trajectory: Trajectory | None = None, confidence: float | None = None
) -> CandidateRule | None:
    """Parse a human correction like \"next time do X\" into a candidate.

    Args:
        text: Human correction text.
        trajectory: Optional trajectory for context (used to infer when trigger).
        confidence: Override confidence; defaults to ``DEFAULT_CORRECTION_CONFIDENCE`` (0.8).

    Returns:
        :class:`CandidateRule` if parsing succeeds, else ``None``.
    """
    text = text.strip()
    if not text:
        return None

    directive: str | None = None
    for pat in _CORRECTION_PATTERNS:
        m = pat.search(text)
        if m:
            directive = m.group(1).strip().rstrip(".")
            break

    if directive is None:
        if len(text.split()) >= 3:
            directive = text
        else:
            return None

    # Infer trigger from failing step output/error first, then failure class, then task
    trigger = "when task fails"
    if trajectory is not None:
        # Look for the failing step (last step with error or output)
        failing_step = None
        for step in reversed(trajectory.steps):
            if step.error:
                failing_step = step
                break
        if failing_step is not None:
            error_snippet = failing_step.error[:80] if failing_step.error else ""
            if error_snippet:
                trigger = f"when {error_snippet}"
            elif failing_step.output:
                trigger = f"when {failing_step.output[:80]}"
        else:
            trigger_set = False
            for step in reversed(trajectory.steps):
                if step.output:
                    trigger = f"when {step.output[:80]}"
                    trigger_set = True
                    break
            if not trigger_set and trajectory.failure_class:
                trigger = f"when {trajectory.failure_class} fails"
            elif not trigger_set and trajectory.task:
                trigger = f"when {trajectory.task[:60]} fails"

    conf = confidence if confidence is not None else DEFAULT_CORRECTION_CONFIDENCE
    conf = max(0.0, min(1.0, conf))

    return CandidateRule(
        when=RuleWhen(trigger=trigger),
        do=RuleDo(directive=directive),
        confidence=conf,
        reasoning=f"human correction: {text[:100]}",
        extraction_pass=1,
        template=None,
    )
