"""OpenAI Agents SDK adapter (F-44) — pre-execution guardrail.

Provides a :class:`PlanGuardrail` that checks the plan is approved before
the agent makes its first tool call. If the plan is not approved, the
guardrail raises a :class:`PlanNotApprovedError`.
"""

from __future__ import annotations

from ..engine import Engine
from ..loop import LoopResult
from ..schema.goal import Goal
from ._audit import AuditEvent, AuditTrail


class PlanNotApprovedError(RuntimeError):
    """Raised when the guardrail detects an unapproved plan."""


class PlanGuardrail:
    """Runner hook that checks plan approval before tool execution.

    Usage::

        guardrail = PlanGuardrail(engine, goal, audit=AuditTrail())
        result = guardrail.check()  # plans and gates on approval
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

    def check(self) -> LoopResult:
        """Run the loop and return the result only if approved.

        This is designed to be called as a pre-tool-call guardrail in the
        OpenAI Agents SDK runner.

        Returns:
            The loop result (with an approved plan).

        Raises:
            PlanNotApprovedError: If the loop did not produce an approved plan.
        """
        if self._result is not None:
            return self._result

        if self._audit is not None:
            self._audit.record(AuditEvent("openai_agents", "plan_requested", plan_id=self._goal.id))

        result = self._engine.plan(self._goal)
        self._result = result

        if not result.is_approved:
            raise PlanNotApprovedError(
                f"plan not approved for goal {self._goal.id!r}: "
                f"status={result.status}, reason={result.reason_code}"
            )

        if self._audit is not None:
            plan_id = result.approved_plan.plan.id if result.approved_plan else None
            self._audit.record(AuditEvent("openai_agents", "plan_approved", plan_id=plan_id))

        return result

    @property
    def result(self) -> LoopResult | None:
        """The cached loop result, or ``None`` if :meth:`check` was not called."""
        return self._result
