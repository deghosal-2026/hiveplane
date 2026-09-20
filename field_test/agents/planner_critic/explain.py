"""Loop-decision explain engine (F-80, CUJ 15): human-readable narratives.

The explain engine takes a plan's stored revision history plus its escalation
record (if any) and produces a structured :class:`ExplainResult` whose
narrative lets a reviewer understand *why* the loop decided what it did —
approved, escalated, or still running — and what changed between revisions.
"""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict

from .reason_codes import REASON_CODE_DESCRIPTIONS
from .store.base import PlanStore
from .types import Escalation, Finding, Severity
from .viz.replay import replay


class ExplainDecision(BaseModel):
    """One revision's place in the loop: approved, escalated, or revised."""

    model_config = ConfigDict(frozen=True)

    version: int
    action: str  # "approved", "escalated", "revised"
    reason: str
    key_findings: list[str]


class ExplainResult(BaseModel):
    """The full explanation for one plan's loop history."""

    model_config = ConfigDict(frozen=True)

    plan_id: str
    summary: str
    narrative: str
    decisions: list[ExplainDecision]


def explain(store: PlanStore, plan_id: str) -> ExplainResult:
    """Produce a narrative explaining why the loop decided what it did.

    Args:
        store: The plan store to read from.
        plan_id: The plan to explain.

    Returns:
        An :class:`ExplainResult` with a one-line summary, multi-paragraph
        narrative, and one :class:`ExplainDecision` per revision.
    """
    try:
        result = replay(store, plan_id)
    except Exception:
        return ExplainResult(
            plan_id=plan_id,
            summary="No history found for plan",
            narrative="The plan has no recorded revision history.",
            decisions=[],
        )

    steps = result.steps
    if not steps:
        return ExplainResult(
            plan_id=plan_id,
            summary="No history found for plan",
            narrative="The plan has no recorded revision history.",
            decisions=[],
        )

    escalation = store.get_escalation(plan_id)
    decisions: list[ExplainDecision] = []

    for i, step in enumerate(steps):
        is_last = i == len(steps) - 1

        if is_last and escalation is not None:
            if escalation.status == "approved":
                action = "approved"
                reason = f"Approved after escalation: {escalation.question}"
            elif escalation.status == "denied":
                action = "denied"
                reason = f"Denied after escalation: {escalation.question}"
            else:
                action = "escalated"
                reason = _escalation_reason(escalation)
        elif is_last and escalation is None:
            action = "approved"
            reason = _approval_reason(step.findings)
        else:
            action = "revised"
            reason = _revision_reason(step.findings)

        key_findings = _format_key_findings(step.findings)
        decisions.append(
            ExplainDecision(
                version=step.version,
                action=action,
                reason=reason,
                key_findings=key_findings,
            )
        )

    summary = _build_summary(decisions, escalation)
    narrative = _build_narrative(decisions, plan_id)
    return ExplainResult(
        plan_id=plan_id,
        summary=summary,
        narrative=narrative,
        decisions=decisions,
    )


def _approval_reason(findings: list[Finding]) -> str:
    """Describe why the plan was approved based on its findings."""
    blockers = [f for f in findings if f.severity is Severity.BLOCKER]
    if not blockers:
        return "All findings were resolved or acknowledged — the plan was approved"
    return (
        f"Approved with {len(blockers)} unresolved "
        f"{'blocker' if len(blockers) == 1 else 'blockers'}"
    )


def _revision_reason(findings: list[Finding]) -> str:
    """Describe why the plan was revised based on its findings."""
    blockers = [f for f in findings if f.severity is Severity.BLOCKER]
    if blockers:
        codes = sorted({f.reason_code for f in blockers})
        descriptions = [REASON_CODE_DESCRIPTIONS.get(c, str(c)) for c in codes]
        return f"Revised: {len(blockers)} blocker(s) found — {'; '.join(descriptions)}"
    warnings = [f for f in findings if f.severity is Severity.WARNING]
    if warnings:
        return "Revised due to warnings requiring further refinement"
    return "Revised for further refinement"


def _escalation_reason(escalation: Escalation) -> str:
    """Derive a human-readable escalation reason from the escalation record."""
    return escalation.question


def _format_key_findings(findings: list[Finding]) -> list[str]:
    """Build one-liner strings from a list of findings."""
    lines: list[str] = []
    for f in findings:
        desc = REASON_CODE_DESCRIPTIONS.get(f.reason_code, f.reason_code)
        if f.task_id:
            lines.append(f"Task {f.task_id}: {desc}")
        else:
            lines.append(f"Plan-level: {desc}")
    return lines


def _build_summary(decisions: list[ExplainDecision], escalation: Escalation | None) -> str:
    """One-line summary of the loop outcome."""
    if not decisions:
        return "No loop decisions recorded"
    last = decisions[-1]
    if last.action == "approved":
        return f"Approved on revision {last.version}"
    if last.action == "escalated":
        return f"Escalated: {last.reason}"
    return f"In progress — last action: {last.action} on revision {last.version}"


def _build_narrative(decisions: list[ExplainDecision], plan_id: str) -> str:
    """Multi-paragraph narrative stringing all decisions together."""
    parts: list[str] = [f"Plan {plan_id!r} went through {len(decisions)} revision(s)."]
    for d in decisions:
        action_label = d.action.capitalize()
        parts.append("")
        parts.append(f"**Revision {d.version} — {action_label}**")
        parts.append(d.reason)
        if d.key_findings:
            parts.append("Key findings:")
            for kf in d.key_findings:
                parts.append(f"  - {kf}")
    return "\n".join(parts)
