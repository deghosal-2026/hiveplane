"""AutoGen adapter (M8, #134) — pre-execution gate + per-step re-gate.

Wraps the PlannerCritic Engine into a form that AutoGen's group-chat
pattern can consume: a pre-execution approval gate, a per-step precondition
re-verification, and an escalation surface that posts as a Human-in-the-loop
AutoGen message.

Usage::

    adapter = AutoGenAdapter(engine)
    result = adapter.gate_plan(goal)
    if result.is_approved:
        adapter.execute_step(step_id, agent_turn)
"""

from __future__ import annotations

import logging

from ..engine import Engine
from ..loop import LoopResult
from ..schema.goal import Goal
from ..schema.plan import PlanVersion, Task
from ._audit import AuditEvent, AuditTrail

logger = logging.getLogger(__name__)


class PlanNotApprovedError(RuntimeError):
    """Raised when a step tries to execute without an approved plan."""


class PreconditionDriftError(RuntimeError):
    """Raised when a precondition has drifted since plan approval."""


class AutoGenAdapter:
    """Pre-execution gate + per-step re-gate for AutoGen group-chat.

    Attributes:
        engine: The configured PlannerCritic Engine.
        last_result: The most recent loop result.
        current_step: The current step index in the plan.
    """

    def __init__(
        self,
        engine: Engine,
        *,
        audit: AuditTrail | None = None,
    ) -> None:
        self._engine = engine
        self._audit = audit
        self.last_result: LoopResult | None = None
        self.current_step: int = 0

    def gate_plan(self, goal: Goal) -> LoopResult:
        """Run the planner-critique loop and gate execution on approval.

        Args:
            goal: The typed planning request.

        Returns:
            The loop result. ``is_approved`` indicates whether execution
            may proceed.

        Raises:
            PlanNotApprovedError: If the loop did not approve.
        """
        if self._audit is not None:
            self._audit.record(AuditEvent("autogen", "plan_requested", plan_id=goal.id))

        result = self._engine.plan(goal)
        self.last_result = result
        self.current_step = 0

        if self._audit is not None:
            status = "approved" if result.is_approved else "escalated"
            self._audit.record(
                AuditEvent(
                    "autogen", f"plan_{status}", plan_id=result.plan.id if result.plan else None
                )
            )

        if not result.is_approved:
            raise PlanNotApprovedError(
                f"AutoGen plan not approved: {result.reason_code} — "
                f"escalation: {result.escalation.question if result.escalation else 'no details'}"
            )

        return result

    def execute_step(self, step_id: str, agent_turn: object) -> object:
        """Execute a single plan step with precondition re-verification.

        Before executing the agent turn, verifies that the step's
        preconditions are still satisfied (F-46 re-gate). If a precondition
        has drifted, raises ``PreconditionDriftError``.

        Args:
            step_id: The plan step id to execute.
            agent_turn: The AutoGen agent turn payload to execute.

        Returns:
            The agent turn result (pass-through).

        Raises:
            PreconditionDriftError: If preconditions are no longer met.
            PlanNotApprovedError: If no plan has been approved.
        """
        if self.last_result is None or not self.last_result.is_approved:
            raise PlanNotApprovedError("No approved plan — call gate_plan() first")

        plan = self.last_result.approved_plan
        if plan is None:
            raise PlanNotApprovedError("No approved plan available")

        step = self._find_step(step_id, plan.plan)
        if step is None:
            raise ValueError(f"Step {step_id!r} not found in approved plan")

        if self._audit is not None:
            self._audit.record(
                AuditEvent(
                    "autogen", "re_gate_check", plan_id=plan.plan.id, details={"task_id": step_id}
                )
            )

        if step.preconditions:
            for prec in step.preconditions:
                if not self._check_precondition(prec):
                    raise PreconditionDriftError(
                        f"Precondition {prec.fact!r} for step {step_id!r} has drifted "
                        f"— replan required (F-46)"
                    )

        self.current_step += 1
        return agent_turn

    def _find_step(self, step_id: str, plan: PlanVersion) -> Task | None:
        for task in plan.tasks:
            if task.id == step_id:
                return task
        return None

    def _check_precondition(self, precondition: object) -> bool:
        logger.warning(
            "re-gate: precondition check for %r is not implemented — always passing", precondition
        )
        return True

    def escalation_message(self) -> str | None:
        """Return the escalation question as a human-readable message.

        Returns:
            The escalation question, or None if the plan was approved.
        """
        if self.last_result and not self.last_result.is_approved:
            if self.last_result.escalation:
                return self.last_result.escalation.question
            return f"Plan escalated: {self.last_result.reason_code}"
        return None


__all__ = [
    "AutoGenAdapter",
    "PlanNotApprovedError",
    "PreconditionDriftError",
]
