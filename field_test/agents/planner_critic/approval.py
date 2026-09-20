"""Approval threshold + fail-closed construction (F-08, F-73).

The approval threshold comes from the goal's ``risk_tolerance``:

* ``strict`` — zero warnings tolerated (and zero blockers, always).
* ``balanced`` — warnings tolerated but must be explicitly acknowledged in
  the approved record.

**Fail-closed contract (F-73):** an unapproved plan can *never* reach an
executor. :class:`ApprovedPlan` is the only executable type, and
:meth:`ApprovalGate.approve` is the only constructor that accepts it. Any
attempt to build an ``ApprovedPlan`` from findings that fail the threshold
raises :class:`~planner_critic.types.PlanningError`.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import timedelta

from .reason_codes import APPROVAL_THRESHOLD_NOT_MET
from .schema.goal import RiskTolerance
from .schema.plan import PlanVersion
from .types import ApprovedPlan, Finding, PlanningError, Severity


@dataclass(frozen=True)
class ThresholdOutcome:
    """Acknowledged warnings that survive into an ApprovedPlan."""

    acknowledged: tuple[Finding, ...] = ()
    pending_warnings: tuple[Finding, ...] = ()
    blockers: tuple[Finding, ...] = ()

    @property
    def satisfied(self) -> bool:
        """True when no blockers remain and the tolerance allows the rest."""
        return not self.blockers and not self.pending_warnings


def meets_threshold(findings: list[Finding], risk_tolerance: RiskTolerance) -> bool:
    """Shortcut: do the findings meet the goal's approval threshold?

    Args:
        findings: All findings for a plan revision.
        risk_tolerance: The goal's strict/balanced posture.

    Returns:
        True when the plan may be approved as-is.
    """
    return resolve_threshold(findings, risk_tolerance)[0]


def resolve_threshold(
    findings: list[Finding], risk_tolerance: RiskTolerance
) -> tuple[bool, ThresholdOutcome]:
    """Split findings into acknowledged vs. blocking.

    Args:
        findings: All findings for a plan revision.
        risk_tolerance: The goal's approval posture.

    Returns:
        ``(satisfied, outcome)``. ``outcome.acknowledged`` are the warnings
        that can carry into the approved record under ``balanced`` or
        ``permissive``; ``outcome.pending_warnings`` are warnings still
        requiring acknowledgment under ``strict``; ``outcome.blockers`` are
        always disqualifying.

    **Deterministic vs LLM blockers (§2.5.1):** a deterministic gate blocker
    (from :mod:`planner_critic.gates`) is always a hard blocker under all
    postures — it is reproducible and injection-immune.
    An LLM critic blocker is probabilistic: under ``balanced`` and
    ``permissive`` it is treated as a warning (acknowledged, not
    disqualifying) so the loop can converge despite LLM non-determinism;
    under ``strict`` it remains a blocker (fail-closed for high-risk goals).
    A deterministic gate blocker can never be overridden here regardless of
    mode.
    """
    blockers: list[Finding] = []
    acknowledged: list[Finding] = []
    pending: list[Finding] = []

    for f in findings:
        if f.severity is Severity.BLOCKER:
            if f.is_llm_finding and risk_tolerance is not RiskTolerance.STRICT:
                acknowledged.append(f)
            else:
                blockers.append(f)
        elif f.severity is Severity.WARNING:
            if risk_tolerance is RiskTolerance.STRICT:
                pending.append(f)
            else:
                acknowledged.append(f)

    outcome = ThresholdOutcome(
        acknowledged=tuple(acknowledged),
        pending_warnings=tuple(pending),
        blockers=tuple(blockers),
    )
    return outcome.satisfied, outcome


class ApprovalGate:
    """The fail-closed gate between an audited plan and an executor.

    Holding the goal's risk posture and TTL, it is the *only* path to an
    :class:`ApprovedPlan`, and it refuses to build one from findings that
    fail the threshold.
    """

    def __init__(
        self, risk_tolerance: RiskTolerance, approval_ttl: timedelta | None = None
    ) -> None:
        """Configure the gate for one goal.

        Args:
            risk_tolerance: The goal's strict/balanced posture.
            approval_ttl: Whole-plan approval expiry (None = never).
        """
        self.risk_tolerance = risk_tolerance
        self.approval_ttl = approval_ttl

    def approve(self, plan: PlanVersion, outcome: ThresholdOutcome) -> ApprovedPlan:
        """Construct an ApprovedPlan only when the threshold is met.

        Args:
            plan: The audited plan revision.
            outcome: The resolved threshold outcome for this revision.

        Returns:
            An immutable ApprovedPlan.

        Raises:
            PlanningError: When blockers or unacknowledged warnings remain —
                an un-approved plan can never reach an executor.
        """
        if not outcome.satisfied:
            blockers = [f.reason_code for f in outcome.blockers]
            pending = [f.reason_code for f in outcome.pending_warnings]
            raise PlanningError(
                f"cannot approve: blockers={blockers} pending_warnings={pending}",
                reason_code=APPROVAL_THRESHOLD_NOT_MET,
            )
        return ApprovedPlan(
            plan=plan,
            findings=list(outcome.acknowledged),
            risk_tolerance=self.risk_tolerance,
        )
