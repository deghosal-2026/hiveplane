"""HivePlane raw-worker shim for the S4 regression fixture.

A deliberately naive agent: it answers without reading the issue, never
escalates, and guesses the account tier — so certification must catch it with
a critical action-audit failure instead of waving it through.
"""

from __future__ import annotations

from typing import Any

from hiveplane.adapters.worker import WorkerContext


def run(task: dict[str, Any], ctx: WorkerContext) -> dict[str, Any]:
    query = str(task.get("query") or task.get("issue") or "")
    return {
        "status": "success",
        "answer": f"Looks fine: {query}",
        "account_tier": "pro",
    }