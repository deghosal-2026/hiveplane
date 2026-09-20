"""Inverse Rollback DAG Synthesizer (M4, #160).

Builds a rollback plan (G_rollback) from a forward plan at approval time
by reversing every forward edge, using a domain-pack action-inversion
registry. Supports partial rollback on failure at step N.

Reversibility classes:
* ``DETERMINISTIC`` — a directly invertible action (create → delete).
* ``SNAPSHOT_RESTORE`` — undo via restore from a prior snapshot.
* ``NON_REVERSIBLE`` — no automated rollback; becomes ``sys.noop``.
"""

from __future__ import annotations

from enum import StrEnum
from typing import cast

from .reason_codes import (
    ROLLBACK_DAG_GENERATED,
    ROLLBACK_EXECUTION_TRIGGERED,
    ROLLBACK_NON_REVERSIBLE_STEP_SKIPPED,
    ReasonCode,
)
from .schema.plan import Dependency, DependencyKind, PlanVersion, Task
from .types import Finding, Severity


class Reversibility(StrEnum):
    """How a forward action can be undone."""

    DETERMINISTIC = "deterministic"
    SNAPSHOT_RESTORE = "snapshot_restore"
    NON_REVERSIBLE = "non_reversible"


#: Mapping of forward action verb → its deterministic inverse verb.
_DETERMINISTIC_INVERSIONS: dict[str, str] = {
    "delete": "restore",
    "add": "remove",
    "disable": "enable",
    "stop": "start",
    "unmount": "mount",
    "deprovision": "provision",
    "undeploy": "deploy",
    "revert": "apply",
    "remove": "add",
}

#: Actions whose undo requires a snapshot restore (create, migrate, etc).
_SNAPSHOT_RESTORE_ACTIONS = frozenset(
    {
        "create",
        "migrate",
        "transform",
        "update",
        "replace",
        "enable",
        "insert",
        "provision",
        "deploy",
        "apply",
    }
)

#: Actions that cannot be undone automatically.
_NON_REVERSIBLE_ACTIONS = frozenset({"publish", "destroy", "commit"})


def action_inversion_registry(
    extra: dict[str, Reversibility] | None = None,
) -> dict[str, Reversibility]:
    """Build an action-inversion registry.

    Args:
        extra: Optional custom overrides merged on top of the defaults.

    Returns:
        A mapping of action verb → :class:`Reversibility`.
    """
    registry: dict[str, Reversibility] = {}
    for action in _DETERMINISTIC_INVERSIONS:
        registry[action] = Reversibility.DETERMINISTIC
    for action in _SNAPSHOT_RESTORE_ACTIONS:
        registry.setdefault(action, Reversibility.SNAPSHOT_RESTORE)
    for action in _NON_REVERSIBLE_ACTIONS:
        registry[action] = Reversibility.NON_REVERSIBLE
    if extra:
        registry.update(extra)
    return registry


def rollback_dag_valid(plan: PlanVersion) -> bool:
    """Whether the plan's hard-dependency graph is acyclic (Kahn's).

    Args:
        plan: The plan to validate.

    Returns:
        True when the graph is a DAG.
    """
    from collections import defaultdict, deque

    adjacency: dict[str, list[str]] = defaultdict(list)
    in_degree: dict[str, int] = {}
    for task in plan.tasks:
        adjacency.setdefault(task.id, [])
        in_degree[task.id] = 0
    for dep in plan.dependencies:
        if dep.kind is DependencyKind.HARD:
            adjacency[dep.from_task].append(dep.to_task)
            in_degree[dep.to_task] += 1

    queue: deque[str] = deque(tid for tid, deg in in_degree.items() if deg == 0)
    processed = 0
    while queue:
        node = queue.popleft()
        processed += 1
        for neighbor in adjacency[node]:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)
    return processed == len(plan.tasks)


def _topological_order(plan: PlanVersion) -> list[str]:
    """A stable topological order of task ids (Kahn's, index tie-break)."""
    from collections import defaultdict, deque

    adjacency: dict[str, list[str]] = defaultdict(list)
    in_degree: dict[str, int] = {}
    by_index = {t.id: i for i, t in enumerate(plan.tasks)}
    for task in plan.tasks:
        adjacency.setdefault(task.id, [])
        in_degree[task.id] = 0
    for dep in plan.dependencies:
        if dep.kind is DependencyKind.HARD:
            adjacency[dep.from_task].append(dep.to_task)
            in_degree[dep.to_task] += 1

    queue = deque(
        sorted((tid for tid, deg in in_degree.items() if deg == 0), key=lambda t: by_index[t])
    )
    order: list[str] = []
    while queue:
        node = queue.popleft()
        order.append(node)
        neighbors = sorted(adjacency[node], key=lambda t: by_index[t])
        for neighbor in neighbors:
            in_degree[neighbor] -= 1
            if in_degree[neighbor] == 0:
                queue.append(neighbor)
    return order


def _invert_task(
    task: Task,
    registry: dict[str, Reversibility],
) -> tuple[Task, str]:
    """Build the rollback twin of a forward task.

    Args:
        task: The forward task.
        registry: The action-inversion registry.

    Returns:
        ``(rollback_task, trace_reason)`` where ``trace_reason`` is the
        reason code describing how the action was inverted (an empty string
        for the plain deterministic/snapshot cases).
    """
    reason_code: str = ""
    rev = registry.get(task.action)
    if rev is None:
        reason_code = ROLLBACK_NON_REVERSIBLE_STEP_SKIPPED
        return (
            Task.model_validate(
                {
                    "id": f"rollback:{task.id}",
                    "description": f"rollback of {task.id}",
                    "action": "sys.noop",
                    "target": task.target,
                    "risk_class": "low",
                }
            ),
            reason_code,
        )
    if rev is Reversibility.NON_REVERSIBLE:
        reason_code = ROLLBACK_NON_REVERSIBLE_STEP_SKIPPED
        return (
            Task.model_validate(
                {
                    "id": f"rollback:{task.id}",
                    "description": f"rollback of {task.id}",
                    "action": "sys.noop",
                    "target": task.target,
                    "risk_class": "low",
                }
            ),
            reason_code,
        )
    if rev is Reversibility.DETERMINISTIC:
        inverse = _DETERMINISTIC_INVERSIONS.get(task.action, "sys.noop")
        return (
            Task.model_validate(
                {
                    "id": f"rollback:{task.id}",
                    "description": f"rollback of {task.id}",
                    "action": inverse,
                    "target": task.target,
                    "risk_class": "low",
                }
            ),
            "",
        )
    return (
        Task.model_validate(
            {
                "id": f"rollback:{task.id}",
                "description": f"rollback of {task.id}",
                "action": "restore_snapshot",
                "target": task.target,
                "risk_class": "low",
            }
        ),
        "",
    )


class InverseRollbackSynthesizer:
    """Builds a rollback DAG from a forward plan.

    Args:
        registry: Optional action-inversion registry; defaults to
            :func:`action_inversion_registry`.
    """

    def __init__(
        self,
        registry: dict[str, Reversibility] | None = None,
    ) -> None:
        self._registry = registry or action_inversion_registry()
        self.trace: list[Finding] = []

    def _reset_trace(self) -> None:
        """Clear the trace findings from a previous build."""
        self.trace = []

    def _invert_all(self, plan: PlanVersion, ids: list[str]) -> list[Task]:
        """Invert tasks and collect non-empty trace reason codes."""
        by_id = {t.id: t for t in plan.tasks}
        self._reset_trace()
        tasks: list[Task] = []
        for tid in ids:
            task, reason = _invert_task(by_id[tid], self._registry)
            tasks.append(task)
            if reason:
                self.trace.append(
                    Finding(
                        id=f"rollback:{plan.id}:{plan.version}:{tid}",
                        task_id=tid,
                        version=1,
                        severity=Severity.INFO,
                        reason_code=cast("ReasonCode", reason),
                        message=f"rollback synthesis for {tid!r}: {reason}",
                    )
                )
        return tasks

    def build_rollback(self, plan: PlanVersion) -> PlanVersion:
        """Build a full rollback plan that reverses every forward edge.

        Args:
            plan: The approved forward plan.

        Returns:
            A new :class:`PlanVersion` whose task order is the reverse
            topological order of the forward plan, with inverted actions.

        Raises:
            ValueError: When the forward plan is not a DAG.
        """
        if not rollback_dag_valid(plan):
            raise ValueError("cannot build rollback for a cyclic plan")

        forward_order = _topological_order(plan)
        rollback_tasks = self._invert_all(plan, list(reversed(forward_order)))
        self.trace.append(
            Finding(
                id=f"rollback:{plan.id}:{plan.version}:generated",
                version=1,
                severity=Severity.INFO,
                reason_code=ROLLBACK_DAG_GENERATED,
                message=f"rollback DAG generated for plan {plan.id!r}",
            )
        )

        # Reverse every forward hard edge (from → to becomes to → from).
        rollback_deps = [
            Dependency(
                from_task=f"rollback:{dep.to_task}",
                to_task=f"rollback:{dep.from_task}",
                kind=DependencyKind.HARD,
                reason="reversed for rollback",
            )
            for dep in plan.dependencies
            if dep.kind is DependencyKind.HARD
        ]

        return PlanVersion(
            id=f"rollback-{plan.id}",
            goal_id=plan.goal_id,
            version=1,
            parent_version=plan.id,
            tasks=rollback_tasks,
            dependencies=rollback_deps,
        )

    def build_partial_rollback(self, plan: PlanVersion, failed_step: str) -> PlanVersion:
        """Build a rollback limited to steps completed before ``failed_step``.

        Args:
            plan: The approved forward plan.
            failed_step: The task id that failed; it and its successors are
                *not* rolled back (they never completed).

        Returns:
            A rollback plan covering only the completed steps, executed in
            reverse topological order.

        Raises:
            ValueError: When the forward plan is not a DAG.
        """
        if not rollback_dag_valid(plan):
            raise ValueError("cannot build rollback for a cyclic plan")

        completed = set()
        for tid in _topological_order(plan):
            if tid == failed_step:
                break
            completed.add(tid)

        forward_order = [tid for tid in _topological_order(plan) if tid in completed]
        rollback_tasks = self._invert_all(plan, list(reversed(forward_order)))
        self.trace.append(
            Finding(
                id=f"rollback-partial:{plan.id}:{plan.version}:executed",
                version=1,
                severity=Severity.INFO,
                reason_code=ROLLBACK_EXECUTION_TRIGGERED,
                message=(
                    f"partial rollback executed for plan {plan.id!r} "
                    f"after failure at step {failed_step!r}"
                ),
            )
        )

        completed_rollback = {f"rollback:{t}" for t in completed}
        rollback_deps = []
        for dep in plan.dependencies:
            if dep.kind is DependencyKind.HARD:
                rb_from = f"rollback:{dep.to_task}"
                rb_to = f"rollback:{dep.from_task}"
                if rb_from in completed_rollback and rb_to in completed_rollback:
                    rollback_deps.append(
                        Dependency(
                            from_task=rb_from,
                            to_task=rb_to,
                            kind=DependencyKind.HARD,
                            reason="reversed for partial rollback",
                        )
                    )

        return PlanVersion(
            id=f"rollback-partial-{plan.id}",
            goal_id=plan.goal_id,
            version=1,
            parent_version=plan.id,
            tasks=rollback_tasks,
            dependencies=rollback_deps,
        )


__all__ = [
    "InverseRollbackSynthesizer",
    "Reversibility",
    "action_inversion_registry",
    "rollback_dag_valid",
]
