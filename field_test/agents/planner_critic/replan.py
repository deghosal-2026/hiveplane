"""Replan semantics (F-16, PRD §2.7b): patch / restart / abort.

When a plan fails mid-execution, the replan function wraps the revised plan
with the correct version metadata to preserve full lineage:

* ``patch`` (default) — the planner revises remaining steps; the result is
  stamped as the next revision with its ``parent_version`` pointing at the
  plan being replaced.
* ``restart`` — the planner re-decomposes from scratch; the lineage is still
  preserved (the new plan's ``parent_version`` points at the old plan), so
  the full history is always traversable.
* ``abort`` — immediately raises :class:`ReplanAbort`; no plan revision is
  created. The caller must surface the abort to the human.

The function does **not** decide *what* the revised plan contains — that is
the planner role's job. It only stamps the correct version metadata so the
revision chain is reconstructable.
"""

from __future__ import annotations

from .schema.goal import Goal, ReplanPolicy
from .schema.plan import PlanVersion
from .types import PlanningError


class ReplanAbort(PlanningError):
    """Raised when the goal's replan policy is ``abort``.

    The caller (loop controller or replan gateway) should surface this to the
    human and stop planning — no plan revision is created.
    """


def replan(goal: Goal, current: PlanVersion, revised: PlanVersion) -> PlanVersion:
    """Wrap a revised plan with the correct version metadata for the policy.

    Args:
        goal: The original goal; carries ``replan_policy``.
        current: The current (failed) plan revision being replaced.
        revised: The planner's revised plan.

    Returns:
        A new :class:`PlanVersion` with the correct version, parent link, and
        ``replan_policy``-compliant structure.

    Raises:
        ReplanAbort: When the goal's policy is ``abort`` — the caller must
            stop planning.
    """
    if goal.replan_policy is ReplanPolicy.ABORT:
        raise ReplanAbort(f"replan policy is abort for goal {goal.id!r}")
    return PlanVersion(
        id=current.id,
        goal_id=current.goal_id,
        version=current.version + 1,
        parent_version=current.id,
        tasks=revised.tasks,
        dependencies=revised.dependencies,
        branches=revised.branches,
    )


__all__ = ["ReplanAbort", "replan"]
