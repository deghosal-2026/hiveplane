"""preconditions_referenced gate — preconditions are grounded.

Every precondition must reference an established fact: either another task
earlier in the ordering that produces/outputs the fact, or a named environment
fact. A precondition that references nothing already established is a
blocker — the step would silently depend on a belief (F-12, Unverified
dependencies family).
"""

from __future__ import annotations

from ..reason_codes import UNVERIFIED_PRECONDITION
from ..schema.plan import PlanVersion, Task
from ..types import Finding, Severity
from .base import BaseGate


class Gate(BaseGate):
    """Flags preconditions that reference no established fact."""

    name = "preconditions_referenced"

    def run(self, plan: PlanVersion) -> list[Finding]:
        """Check every precondition is grounded in an earlier fact.

        Args:
            plan: The typed plan to audit.

        Returns:
            One finding per ungrounded precondition.
        """
        findings: list[Finding] = []

        #: Facts established by tasks that appear *earlier* in the plan list.
        established_before: dict[str, frozenset[str]] = {}
        run_facts: list[frozenset[str]] = []
        for task in plan.tasks:
            established_before[task.id] = frozenset().union(*run_facts)
            run_facts.append(self._task_facts(task))

        for task in plan.tasks:
            available = established_before[task.id]
            for precondition in task.preconditions:
                grounded = False
                if precondition.probe is not None:
                    grounded = True
                elif precondition.established_by is not None:
                    grounding = precondition.established_by
                    grounded = (
                        grounding in available
                        or grounding.startswith("env:")
                        or grounding == "env"
                        or grounding in ("environment", "system", "infra")
                    )
                if not grounded:
                    findings.append(
                        Finding(
                            id=f"preconditions_referenced:{plan.id}:{plan.version}:{task.id}:{precondition.fact}",
                            task_id=task.id,
                            version=plan.version,
                            severity=Severity.BLOCKER,
                            reason_code=UNVERIFIED_PRECONDITION,
                            message=(
                                f"precondition {precondition.fact!r} on task {task.id!r} "
                                "references no established fact from an earlier task"
                            ),
                            suggested_fix=(
                                f"Point the precondition at an earlier task "
                                f"that establishes {precondition.fact!r}"
                            ),
                        )
                    )
        return findings

    def _task_facts(self, task: Task) -> frozenset[str]:
        """Collect the facts a single task establishes.

        Args:
            task: The task whose outputs may ground later preconditions.

        Returns:
            The set of fact keys this task establishes: its own id, the
            environment fact named by its target, any verification output,
            and any precondition facts it itself declares (so a task that
            checks 'db_healthy' establishes 'db_healthy' for later tasks).
        """
        facts: set[str] = {task.id, f"env:{task.target}"}
        if task.verification is not None:
            facts.add(f"verified:{task.verification.what}")
            facts.add(task.verification.what)
        for pre in task.preconditions:
            if pre.fact:
                facts.add(pre.fact)
            if pre.established_by:
                facts.add(pre.established_by)
        return frozenset(facts)
