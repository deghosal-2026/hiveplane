"""HivePlane raw-worker shim for the S5 over-budget fixture.

Makes exactly one governed model call so real, priced token usage is recorded
through the budget service. Paired with a tiny `per_run_usd`, the priced usage
exceeds the run ceiling and the control plane fails the run on budget — the
live demonstration that budget enforcement blocks expensive work.
"""

from __future__ import annotations

from typing import Any

from hiveplane.adapters.worker import WorkerContext
from hiveplane.llm.models import Message


def run(task: dict[str, Any], ctx: WorkerContext) -> dict[str, Any]:
    completion = ctx.complete(
        [Message(role="user", content="Reply with the single word: ok")]
    )
    return {"status": "completed", "response": completion.content[:64]}