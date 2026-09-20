"""PydanticAI adapter (F-42) — approval guard for PydanticAI agents.

Provides an :class:`ApprovalGuard` that can be used as a ``@guard`` tool or
pre-execution hook to block tool calls until a plan is approved.
"""

from __future__ import annotations

from ..engine import Engine
from ..loop import LoopResult
from ..schema.goal import Goal
from ._audit import AuditEvent, AuditTrail


class PlanNotApprovedError(RuntimeError):
    """Raised when a tool call is attempted without an approved plan."""


class ApprovalGuard:
    """Guard that gates tool execution on plan approval.

    Usage::

        guard = ApprovalGuard(engine, goal, audit=AuditTrail())
        guard.guard(ctx)  # called before first tool execution
    """

    def __init__(
        self,
        engine: Engine,
        goal: Goal,
        *,
        audit: AuditTrail | None = None,
    ) -> None:
        self._engine = engine
        self._goal = goal
        self._audit = audit
        self._result: LoopResult | None = None

    def guard(self, ctx: object | None = None) -> LoopResult:
        """Run the PlannerCritic loop and return the result.

        This is the pre-tool-call guard: it ensures a plan exists and is
        approved before any downstream tool executes. If planning has already
        succeeded, the cached result is returned.

        Args:
            ctx: Optional PydanticAI run context (ignored in this base
                implementation; subclasses may inspect it).

        Returns:
            The loop result.

        Raises:
            PlanNotApprovedError: If the loop did not produce an approved plan.
        """
        if self._result is not None:
            return self._result

        if self._audit is not None:
            self._audit.record(AuditEvent("pydantic_ai", "plan_requested", plan_id=self._goal.id))

        result = self._engine.plan(self._goal)
        self._result = result

        if not result.is_approved:
            raise PlanNotApprovedError(
                f"plan not approved for goal {self._goal.id!r}: "
                f"status={result.status}, reason={result.reason_code}"
            )

        if self._audit is not None:
            plan_id = result.approved_plan.plan.id if result.approved_plan else None
            self._audit.record(AuditEvent("pydantic_ai", "plan_approved", plan_id=plan_id))

        return result
