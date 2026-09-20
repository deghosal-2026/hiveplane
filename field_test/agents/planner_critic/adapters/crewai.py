"""CrewAI adapter (F-43) — task interceptor that verifies plan coverage.

Wraps CrewAI task execution so that before each task runs, the adapter
verifies the approved plan includes a matching step. If the task description
does not appear in any planned task, a :class:`TaskNotInPlanError` is raised.
"""

from __future__ import annotations

from ..types import ApprovedPlan
from ._audit import AuditEvent, AuditTrail


class TaskNotInPlanError(RuntimeError):
    """Raised when a CrewAI task is not found in the approved plan."""


class PlanAwareTaskInterceptor:
    """Interceptor that verifies each task against the approved plan.

    Usage::

        interceptor = PlanAwareTaskInterceptor(approved_plan, audit=AuditTrail())
        interceptor.before_execution(task_description)
    """

    def __init__(
        self,
        approved_plan: ApprovedPlan,
        *,
        audit: AuditTrail | None = None,
    ) -> None:
        self._approved = approved_plan
        self._audit = audit

    def verify_task(self, task_description: str) -> bool:
        """Check that the task description appears in the approved plan.

        Args:
            task_description: The description of the CrewAI task about to run.

        Returns:
            ``True`` if a matching task is found in the approved plan.

        Raises:
            TaskNotInPlanError: If no task in the plan has a matching
                description (substring match).
        """
        plan_tasks = self._approved.plan.tasks
        task_descriptions = [t.description.lower() for t in plan_tasks]
        query = task_description.lower()

        found = any(query in td or td in query for td in task_descriptions)

        if self._audit is not None:
            self._audit.record(
                AuditEvent(
                    "crewai",
                    "re_gate_check",
                    plan_id=self._approved.plan.id,
                    details={
                        "task_description": task_description,
                        "found": found,
                    },
                )
            )

        if not found:
            raise TaskNotInPlanError(
                f"task {task_description!r} not found in approved plan '{self._approved.plan.id}'"
            )

        return True

    def before_execution(self, task_description: str) -> None:
        """Called before a CrewAI task executes.

        Args:
            task_description: The task description to verify.

        Raises:
            TaskNotInPlanError: If the task is not in the approved plan.
        """
        self.verify_task(task_description)
