"""verification_present gate — high-risk steps carry a verification step.

A high-blast-radius task (RISK high/critical, or blast_radius
high/critical) that lacks a :class:`VerificationStep` is unverifiable after
execution — a blocker (F-15).
"""

from __future__ import annotations

from ..reason_codes import MISSING_VERIFICATION
from ..schema.plan import PlanVersion, Task
from ..types import Finding, Severity
from .base import BaseGate


def _is_high_blast(task: Task) -> bool:
    """True when the task is high-risk by risk_class or blast_radius."""
    return task.risk_class.is_high_risk or task.blast_radius in ("high", "critical")


class Gate(BaseGate):
    """Flags high-risk tasks without a verification step."""

    name = "verification_present"

    def run(self, plan: PlanVersion) -> list[Finding]:
        """Check high-blast-radius tasks carry verification.

        Args:
            plan: The typed plan to audit.

        Returns:
            One finding per high-risk task missing a verification step.
        """
        findings: list[Finding] = []
        for task in plan.tasks:
            if _is_high_blast(task) and task.verification is None:
                findings.append(
                    Finding(
                        id=f"verification_present:{plan.id}:{plan.version}:{task.id}",
                        task_id=task.id,
                        version=plan.version,
                        severity=Severity.BLOCKER,
                        reason_code=MISSING_VERIFICATION,
                        message=f"high-risk task {task.id!r} has no verification step",
                        suggested_fix=f"Add a VerificationStep to task {task.id!r}",
                    )
                )
        return findings
