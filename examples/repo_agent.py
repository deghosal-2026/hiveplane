"""Reference raw-worker workload: summarize open pull requests.

Demonstrates the worker contract: route tool calls through the control-plane
boundary, report usage, and return a result.
"""

from __future__ import annotations

from typing import Any

from hiveplane.adapters.worker import WorkerContext


def run(task: dict[str, Any], ctx: WorkerContext) -> dict[str, Any]:
    """List open pull requests and report a short summary."""
    pulls = ctx.tool_call(
        "mcp.github.list_pull_requests",
        host="api.github.com",
        output="[]",
    )
    ctx.report_usage(input_tokens=120, output_tokens=40, tool_calls=1, cost_usd=0.0)
    return {"repo": task.get("repo"), "tool": pulls.tool_id, "status": "summarized"}
