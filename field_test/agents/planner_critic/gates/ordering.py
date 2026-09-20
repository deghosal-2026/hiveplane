"""ordering_sane gate — no task is ordered before a hard dependency.

Uses topological order of hard dependencies as the reference order: a task
must not appear in the plan's task list earlier than one of its hard
prerequisites. Reports one finding per violated task (F-12).
"""

from __future__ import annotations

from ..reason_codes import UNSAFE_ORDERING
from ..schema.plan import DependencyKind, PlanVersion
from ..types import Finding, Severity
from .base import BaseGate


class Gate(BaseGate):
    """Flags tasks positioned before a hard dependency."""

    name = "ordering_sane"

    def run(self, plan: PlanVersion) -> list[Finding]:
        """Check list-ordering vs hard-dependency ordering.

        Args:
            plan: The typed plan to audit.

        Returns:
            One finding per task whose linear position violates its hard
            dependencies. Empty when ordering is consistent.
        """
        id_to_index = {task.id: index for index, task in enumerate(plan.tasks)}
        hard_from_to = {
            (dep.from_task, dep.to_task)
            for dep in plan.dependencies
            if dep.kind is DependencyKind.HARD
        }
        if not hard_from_to:
            return []

        findings: list[Finding] = []
        for pred, succ in sorted(hard_from_to):
            if id_to_index[pred] > id_to_index[succ]:
                findings.append(
                    Finding(
                        id=f"ordering_sane:{plan.id}:{plan.version}:{pred}",
                        task_id=succ,
                        version=plan.version,
                        severity=Severity.BLOCKER,
                        reason_code=UNSAFE_ORDERING,
                        message=f"task {succ!r} is ordered before its hard dependency {pred!r}",
                        suggested_fix=f"Reorder the plan so task {succ!r} appears after {pred!r}",
                        edge_id=f"{pred}->{succ}",
                        observed_state=f"task {succ!r} at position {id_to_index[succ]} "
                        f"appears before dependency {pred!r} at position {id_to_index[pred]}",
                        evidence_refs=[f"hard_dep:{pred}->{succ}"],
                    )
                )
        return findings
