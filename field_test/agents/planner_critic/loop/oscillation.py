"""Structural oscillation detection and auto-converge (M2, #152).

Detects when the planner cycles between structurally identical plan shapes
(``plan_oscillation_detected``) — a pattern that neither F-06 (content-level
convergence) nor F-07 (new-blocker regression) catches. When
``converge_policy`` is ``auto_converge``, the non-oscillating tasks are
approved and only the cycling subset is escalated.

Key concepts:
* :func:`compute_plan_signature` — content-agnostic structural hash.
  Task descriptions and actions are excluded; task ids, dependencies,
  parallel-group membership, verification/rollback presence, and
  risk-class distribution are included.
* :func:`detect_oscillation` — given a deque of the last K signatures,
  returns ``True`` when any signature appears at least twice in the window.
"""

from __future__ import annotations

from collections import Counter

from ..schema.plan import PlanVersion


def compute_plan_signature(plan: PlanVersion) -> str:
    """Content-agnostic structural hash of a plan.

    Two plans with the same structure but different task descriptions or
    actions produce the same signature. The hash is built from:

    * task ids (sorted),
    * hard-dependency edges (sorted, ``(from, to, kind)``),
    * parallel-group membership (sorted group → sorted member ids),
    * per-task risk class + blast radius + verification/rollback presence,
    * branch structure (sorted branch tuples).
    """
    task_ids = sorted(task.id for task in plan.tasks)

    dep_edges = sorted((dep.from_task, dep.to_task, dep.kind.value) for dep in plan.dependencies)

    by_group: dict[str, list[str]] = {}
    for task in plan.tasks:
        if task.parallel_group is not None:
            by_group.setdefault(task.parallel_group, []).append(task.id)
    groups = sorted((group, sorted(members)) for group, members in by_group.items())

    task_shapes = sorted(
        (
            task.id,
            task.risk_class.value,
            task.blast_radius,
            task.verification is not None,
            task.rollback is not None,
        )
        for task in plan.tasks
    )

    branches = sorted(
        (branch.id, branch.kind.value, sorted(branch.tasks), branch.join.value)
        for branch in plan.branches
    )

    return f"{task_ids}|{dep_edges}|{groups}|{task_shapes}|{branches}"


def detect_oscillation(signatures: list[str], window: int) -> bool:
    """Return True when any structural signature appears ≥2 times in the window.

    Args:
        signatures: A FIFO queue of the last ``window`` plan signatures.
        window: How many recent revisions are inspected (K in the spec).

    Returns:
        True when a signature appears at least twice — the plan is cycling.
    """
    if len(signatures) < window:
        return False
    recent = signatures[-window:]
    counts = Counter(recent)
    return any(count >= 2 for count in counts.values())


def oscillating_task_ids(plan_a: PlanVersion, plan_b: PlanVersion) -> frozenset[str]:
    """Return task ids that differ between two oscillating structural shapes.

    A task is "oscillating" when it appears in exactly one of the two plans
    (structural shape changed). Tasks present in both with the same shape
    are non-oscillating (the intersection).

    Args:
        plan_a: One oscillating shape.
        plan_b: The other oscillating shape.

    Returns:
        The task ids that are *not* common to both shapes — these are the
        oscillating tasks that the auto-converge mode should escalate.
    """
    ids_a = {t.id for t in plan_a.tasks}
    ids_b = {t.id for t in plan_b.tasks}
    return frozenset(ids_a.symmetric_difference(ids_b))


__all__ = [
    "compute_plan_signature",
    "detect_oscillation",
    "oscillating_task_ids",
]
