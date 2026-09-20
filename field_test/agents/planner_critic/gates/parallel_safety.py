"""parallel_safety gate — F-15 unsafe-parallelization audit.

When tasks share a ``parallel_group`` (they run concurrently), a *soft*
dependency between two group members is safe (advisory, ordering is not
enforced by the engine), but a **hard** dependency between them is a
contradiction already rejected at schema level. What this gate audits is the
*safety* of running members concurrently: two high-blast-radius tasks in one
group can both corrupt state before anyone rolls back — a blocker.

Also flags a branch whose fan-out tasks in one group are mutually high-risk.
"""

from __future__ import annotations

from collections import defaultdict

from ..reason_codes import UNSAFE_PARALLELIZATION
from ..schema.plan import PlanVersion, Task
from ..types import Finding, Severity
from .base import BaseGate


def _is_high_blast(task: Task) -> bool:
    """True for tasks flagged high-risk by class or blast radius."""
    return task.risk_class.is_high_risk or task.blast_radius in ("high", "critical")


class Gate(BaseGate):
    """Flags unsafe concurrency: high-risk tasks racing in one group."""

    name = "parallel_safety"

    def run(self, plan: PlanVersion) -> list[Finding]:
        """Check each parallel group for concurrency hazards.

        Args:
            plan: The typed plan to audit.

        Returns:
            One finding per group holding two or more high-blast tasks.
        """
        by_group: dict[str, list[Task]] = defaultdict(list)
        for task in plan.tasks:
            if task.parallel_group is not None:
                by_group[task.parallel_group].append(task)

        findings: list[Finding] = []
        for group in sorted(by_group):
            members = by_group[group]
            high_blast = [t.id for t in members if _is_high_blast(t)]
            if len(high_blast) >= 2:
                findings.append(
                    Finding(
                        id=f"parallel_safety:{plan.id}:{plan.version}:{group}",
                        task_id=high_blast[0],
                        version=plan.version,
                        severity=Severity.BLOCKER,
                        reason_code=UNSAFE_PARALLELIZATION,
                        message=(
                            f"parallel_group {group!r} runs high-blast tasks "
                            f"{sorted(high_blast)} concurrently; failure cannot be contained"
                        ),
                        suggested_fix=(
                            f"Move all but one of {sorted(high_blast)} out of group {group!r}, "
                            "or serialize the high-risk operations"
                        ),
                    )
                )
        return findings
