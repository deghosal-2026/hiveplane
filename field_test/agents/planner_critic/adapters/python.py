"""Raw Python adapter (F-40) — a thin wrapper around :class:`Engine`.

Provides a module-level :func:`plan` function and a lightweight
:class:`PlannerCriticPlan` class so that consumers that do not use an agent
framework can still invoke the PlannerCritic loop from plain Python.
"""

from __future__ import annotations

from ..engine import Engine
from ..loop import LoopResult
from ..schema.goal import Goal
from ._audit import AuditEvent, AuditTrail


def plan(
    engine: Engine,
    goal: Goal,
    *,
    audit: AuditTrail | None = None,
) -> LoopResult:
    """Run the PlannerCritic loop for a goal.

    Args:
        engine: A configured :class:`Engine` with planner and critic roles.
        goal: The typed planning request.
        audit: Optional audit trail to record lifecycle events.

    Returns:
        The loop outcome (approved plan or escalation).
    """
    if audit is not None:
        audit.record(AuditEvent("raw", "plan_requested", plan_id=goal.id))
    result = engine.plan(goal)
    if audit is not None and result.is_approved:
        ap = result.approved_plan
        if ap is not None:
            audit.record(AuditEvent("raw", "plan_approved", plan_id=ap.plan.id))
    return result


class PlannerCriticPlan:
    """Convenience wrapper that binds an engine for repeated use.

    Usage::

        planner = PlannerCriticPlan(engine, audit=AuditTrail())
        result = planner.plan(goal)
    """

    def __init__(
        self,
        engine: Engine,
        *,
        audit: AuditTrail | None = None,
    ) -> None:
        self._engine = engine
        self._audit = audit

    def plan(self, goal: Goal) -> LoopResult:
        return plan(self._engine, goal, audit=self._audit)
