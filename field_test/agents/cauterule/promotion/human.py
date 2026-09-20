"""Human-review mode — all verdicts generate an evidence summary."""

from __future__ import annotations

from cauterule.linter.orchestrator import LinterResult
from cauterule.models.candidate import CandidateRule
from cauterule.models.decision import PromotionDecision
from cauterule.models.evidence import EvidenceReport


def _build_evidence_summary(
    candidate: CandidateRule,
    evidence_report: EvidenceReport,
    linter_result: LinterResult,
) -> str:
    parts: list[str] = [
        f"Rule: when={candidate.when.trigger!r} do={candidate.do.directive!r}",
        f"Confidence: {candidate.confidence:.2f}",
        f"Evidence verdict: {evidence_report.verdict}",
        f"Precision: {evidence_report.precision:.2f}",
        f"Recall: {evidence_report.recall:.2f}",
        f"Failures prevented: {len(evidence_report.failures_prevented)}",
        f"Successes broken: {len(evidence_report.successes_broken)}",
        f"Linter passed: {linter_result.passed}",
    ]
    if linter_result.warnings:
        parts.append(f"Linter warnings: {', '.join(linter_result.warnings)}")
    if candidate.reasoning:
        parts.append(f"Extraction reasoning: {candidate.reasoning}")
    return "\n".join(parts)


def human_review(
    candidate: CandidateRule,
    evidence_report: EvidenceReport,
    linter_result: LinterResult,
) -> PromotionDecision:
    """Prepare a decision for human review.

    All verdicts produce a structured evidence summary for a human to make
    the final call. The decision is always ``"needs_review"`` — the human
    reviewer uses the summary to decide promote / reject.

    Args:
        candidate: The candidate rule under review.
        evidence_report: Replay-evidence report for the candidate.
        linter_result: Linter result for the candidate.

    Returns:
        A :class:`PromotionDecision` with verdict ``"needs_review"`` and
        a detailed evidence summary.
    """
    summary = _build_evidence_summary(candidate, evidence_report, linter_result)
    return PromotionDecision(
        verdict="needs_review",
        evidence_summary=summary,
        approver="human",
        linter_warnings=linter_result.warnings,
    )
