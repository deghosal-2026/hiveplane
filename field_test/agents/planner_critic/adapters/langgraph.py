"""LangGraph adapter (F-41) — pre-execution approval guard for graph nodes.

Provides a :class:`ApprovalHook` that can be used as a LangGraph node or
callback to verify that the current plan is approved before each graph step
executes. If no ``ApprovedPlan`` is available, the hook raises
:class:`PlanNotApprovedError`.
"""

from __future__ import annotations

from collections.abc import Callable

from ..types import ApprovedPlan
from ._audit import AuditEvent, AuditTrail


class PlanNotApprovedError(RuntimeError):
    """Raised when a graph step tries to execute without an approved plan."""


class ApprovalHook:
    """Pre-execution guard that checks for an approved plan.

    Usage::

        hook = ApprovalHook(approved_plan)
        graph = StateGraph(AgentState)
        graph.add_node("step1", with_approval_hook(hook, my_step_fn))
    """

    def __init__(
        self,
        approved_plan: ApprovedPlan | None = None,
        *,
        audit: AuditTrail | None = None,
    ) -> None:
        self._approved = approved_plan
        self._audit = audit

    @property
    def approved_plan(self) -> ApprovedPlan | None:
        return self._approved

    def check(self) -> ApprovedPlan:
        """Return the approved plan or raise :class:`PlanNotApprovedError`.

        Returns:
            The approved plan if one is set.

        Raises:
            PlanNotApprovedError: If no approved plan is available.
        """
        if self._audit is not None:
            plan_id = self._approved.plan.id if self._approved else None
            self._audit.record(AuditEvent("langgraph", "re_gate_check", plan_id=plan_id))
        if self._approved is None:
            raise PlanNotApprovedError("no ApprovedPlan set — cannot execute graph step")
        return self._approved


def with_approval_hook(
    hook: ApprovalHook,
    fn: Callable[..., object],
) -> Callable[..., object]:
    """Decorate a graph node function to run the approval check first.

    Args:
        hook: The approval hook to check before the node runs.
        fn: The graph node function (callable).

    Returns:
        A wrapped callable that checks approval before invoking ``fn``.
    """

    def wrapped(*args: object, **kwargs: object) -> object:
        hook.check()
        return fn(*args, **kwargs)

    return wrapped
