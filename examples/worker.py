"""Reference hello-agent workload scaffolded by ``hiveplane init`` (M23, #119).

Demonstrates the worker contract: call the model through the control-plane seam
and return a structured result. The greeting is deterministic so the seeded
demo works out of the box; the model call exercises the seam.
"""

from __future__ import annotations

from typing import Any

from hiveplane.adapters.worker import WorkerContext


def run(task: dict[str, Any], ctx: WorkerContext) -> dict[str, Any]:
    """Greet the task's ``name`` and return a structured result."""
    name = str(task.get("name", "world"))
    note = ctx.complete(f"Greet the user named {name} in a friendly sentence.")
    return {"greeting": f"hello, {name}", "note": note.content}
