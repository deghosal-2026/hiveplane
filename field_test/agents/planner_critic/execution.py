"""Plan-execution link + failure tagging (F-50, F-51, F-52).

When an :class:`ApprovedPlan` is executed, every task outcome is recorded as
an :class:`ExecutionTrace` linked back to the approved revision. Failures are
classified as ``planning`` (the critic missed a flaw — the trace carries the
linked finding) or ``execution`` (the plan was sound but the step failed).
The classification drives the missed-critique forensics (T7).
"""

from __future__ import annotations

import uuid
from typing import Literal

from .store.base import PlanStore
from .types import ApprovedPlan, ExecutionTrace, Finding


class ExecutionRecorder:
    """Record per-task execution outcomes against an approved plan.

    Args:
        store: The plan store that persists execution traces and links.
    """

    def __init__(self, store: PlanStore) -> None:
        """Bind the recorder to its backing store."""
        self._store = store

    def record(
        self,
        approved: ApprovedPlan,
        task_id: str,
        outcome: str,
        linked_finding: Finding | None = None,
    ) -> ExecutionTrace:
        """Record one task's execution outcome and link it to the approved plan.

        Args:
            approved: The approved plan that authorized execution.
            task_id: The task that was executed.
            outcome: The outcome string (e.g. ``"ok"`` or ``"failed"``).
            linked_finding: Optional finding that was missed by the critic
                (sets ``failure_class`` to ``planning``).

        Returns:
            The persisted :class:`ExecutionTrace`.
        """
        failure_class = self.classify_failure(linked_finding) if outcome == "failed" else None
        trace = ExecutionTrace(
            id=f"trace:{uuid.uuid4().hex[:12]}",
            plan_id=approved.plan.id,
            task_id=task_id,
            outcome=outcome,
            failure_class=failure_class,
            linked_finding_id=linked_finding.id if linked_finding else None,
        )
        self._store.put_execution_trace(trace)
        self._store.link(approved.plan.id, approved.plan.version, trace.id)
        return trace

    def get_traces(self, plan_id: str) -> list[ExecutionTrace]:
        """Return all recorded execution steps for a plan, in order.

        Args:
            plan_id: The plan whose traces to fetch.

        Returns:
            The recorded execution steps, in insertion order.
        """
        return self._store.get_execution_traces(plan_id)

    @staticmethod
    def classify_failure(
        linked_finding: Finding | None,
    ) -> Literal["planning", "execution"]:
        """Classify a failure as planning or execution blame.

        Args:
            linked_finding: A finding the critic missed (if any).

        Returns:
            ``planning`` when a finding was linked (the critic missed it),
            ``execution`` when no finding was linked (the plan was sound).
        """
        if linked_finding is not None:
            return "planning"
        return "execution"


__all__ = ["ExecutionRecorder"]
