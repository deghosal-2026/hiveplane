"""Plan-graph export (F-75): Mermaid + JSON DAG rendering.

Renders a :class:`PlanVersion`'s task DAG as a Mermaid flowchart or a JSON
document. The DAG is built from tasks (nodes) and dependencies (edges);
parallel groups and branches are preserved as node metadata in the JSON
export so downstream tooling can reconstruct the full plan structure.
"""

from __future__ import annotations

from ..schema.plan import PlanVersion


def to_mermaid(plan: PlanVersion) -> str:
    """Render the plan's task DAG as a Mermaid flowchart.

    Args:
        plan: The plan revision to render.

    Returns:
        A Mermaid ``graph TD`` string with one node per task and edges for
        each dependency.
    """
    lines = ["graph TD"]
    for task in plan.tasks:
        label = task.description.replace('"', "'")
        lines.append(f'    {task.id}["{label}"]')
    for dep in plan.dependencies:
        style = " -->" if dep.kind.value == "hard" else " -.->"
        lines.append(f"    {dep.from_task}{style} {dep.to_task}")
    return "\n".join(lines) + "\n"


def to_json(plan: PlanVersion) -> dict[str, object]:
    """Render the plan's task DAG as a JSON-serializable dict.

    Args:
        plan: The plan revision to render.

    Returns:
        A dict with ``plan_id``, ``version``, ``nodes`` (list of task dicts),
        and ``edges`` (list of dependency dicts).
    """
    nodes: list[dict[str, object]] = []
    for task in plan.tasks:
        nodes.append(
            {
                "id": task.id,
                "description": task.description,
                "risk_class": task.risk_class.value,
                "parallel_group": task.parallel_group,
            }
        )
    edges: list[dict[str, object]] = []
    for dep in plan.dependencies:
        edges.append(
            {
                "from": dep.from_task,
                "to": dep.to_task,
                "kind": dep.kind.value,
                "reason": dep.reason,
            }
        )
    return {
        "plan_id": plan.id,
        "version": plan.version,
        "nodes": nodes,
        "edges": edges,
    }


__all__ = ["to_json", "to_mermaid"]
