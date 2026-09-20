"""no_dep_cycles gate — the dependency graph is a DAG.

Checks that hard dependencies form no cycle. A cycle means no topological
order exists, so the plan can never be executed safely (F-12).

Uses Kahn's algorithm to compute a topological order broadly; a leftover
node (or **all** remaining nodes when a cycle exists) is a cycle member.
"""

from __future__ import annotations

from collections import defaultdict, deque

from ..reason_codes import DEPENDENCY_CYCLE
from ..schema.plan import DependencyKind, PlanVersion
from ..types import Finding, Severity
from .base import BaseGate


class Gate(BaseGate):
    """Flags cycles in the hard-dependency graph."""

    name = "no_dep_cycles"

    def run(self, plan: PlanVersion) -> list[Finding]:
        """Detect a cycle in the plan's hard dependencies.

        Args:
            plan: The typed plan to audit.

        Returns:
            A single cycle finding when any cycle exists; empty when the
            hard-dependency graph is a DAG.
        """
        hard_edges = [
            (dep.from_task, dep.to_task)
            for dep in plan.dependencies
            if dep.kind is DependencyKind.HARD
        ]
        if not hard_edges:
            return []

        adjacency: dict[str, list[str]] = defaultdict(list)
        in_degree: dict[str, int] = defaultdict(int)
        for task in plan.tasks:
            adjacency.setdefault(task.id, [])
            in_degree.setdefault(task.id, 0)
        for src, dst in hard_edges:
            adjacency[src].append(dst)
            in_degree[dst] += 1

        queue: deque[str] = deque(tid for tid, deg in in_degree.items() if deg == 0)
        processed = 0
        while queue:
            node = queue.popleft()
            processed += 1
            for neighbor in adjacency[node]:
                in_degree[neighbor] -= 1
                if in_degree[neighbor] == 0:
                    queue.append(neighbor)

        if processed != len(plan.tasks):
            in_cycle = [tid for tid, deg in in_degree.items() if deg > 0]
            return [
                Finding(
                    id=f"no_dep_cycles:{plan.id}:{plan.version}",
                    version=plan.version,
                    severity=Severity.BLOCKER,
                    reason_code=DEPENDENCY_CYCLE,
                    message=f"dependency cycle detected among tasks: {sorted(in_cycle)}",
                    suggested_fix="Break the cycle by removing or re-directing a hard dependency",
                )
            ]
        return []
