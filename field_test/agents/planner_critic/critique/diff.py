"""Diff-aware critique helpers (F-78, PRD §2.5.3).

On revision N>1 the critic re-audits only **changed tasks + their transitive
dependents** rather than the whole plan — a cost optimization aligned with the
budget. The changed-task set is computed from the plan diff
(:class:`~planner_critic.store.base.PlanDiff`); a full re-critique remains
available via ``critic.mode=llm-every-revision`` when audit depth justifies it.
"""

from __future__ import annotations

from collections.abc import Sequence

from ..schema.plan import PlanVersion
from ..store.base import PlanDiff


def changed_tasks(
    plan: PlanVersion,
    diff: PlanDiff | None,
) -> list[str]:
    """The task ids in ``plan`` that changed since its parent.

    Args:
        plan: The current (newer) revision.
        diff: The structural diff of the parent→current transition, or None
            when there is no parent (revision 1 scouts the full plan).

    Returns:
        The changed-task ids. When ``diff`` is None (the root revision) the
        whole plan is the scope — every task.
    """
    if diff is None:
        return [t.id for t in plan.tasks]
    return list(diff.added_task_ids) + list(diff.changed_task_ids)


def dependent_closure(plan: PlanVersion, changed_task_ids: Sequence[str]) -> list[str]:
    """Expand a changed-task set to include transitive dependents (F-78).

    Args:
        plan: The plan whose dependency DAG defines reachability.
        changed_task_ids: The tasks that actually changed.

    Returns:
        The changed tasks plus every task that (transitively) depends on them.
    """
    closure = set(changed_task_ids)
    rev: dict[str, list[str]] = {}
    for dep in plan.dependencies:
        rev.setdefault(dep.from_task, []).append(dep.to_task)

    frontier = list(closure)
    while frontier:
        current = frontier.pop()
        for dependent in rev.get(current, []):
            if dependent not in closure:
                closure.add(dependent)
                frontier.append(dependent)
    return sorted(closure)


def audit_scope(plan: PlanVersion, diff: PlanDiff | None) -> list[str]:
    """The task scope the critic must re-audit for this revision (F-78).

    Args:
        plan: The current revision.
        diff: Structural diff vs the parent (None on the root revision).

    Returns:
        The changed tasks + dependents; the full plan when there is no diff.
    """
    return dependent_closure(plan, changed_tasks(plan, diff))


def scope_between(parent: PlanVersion | None, plan: PlanVersion) -> list[str]:
    """The audit scope for ``plan`` given its parent (F-78, loop integration).

    Args:
        parent: The previous revision, or None on the root revision.
        plan: The current revision to audit.

    Returns:
        The changed-task closure when a parent exists; the full plan on the
        root revision.
    """
    if parent is None:
        return [t.id for t in plan.tasks]
    from ..store.base import _compute_diff

    return audit_scope(plan, _compute_diff(parent, plan))
