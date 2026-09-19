"""Reference raw-worker workload: classify pull-request risk with a real model.

Demonstrates the worker contract: route a tool call through the control-plane
boundary to fetch real data, invoke the model through the governed seam, and
return a structured result. Usage is reported by the seam, not the agent.
"""

from __future__ import annotations

import json
from typing import Any

from hiveplane.adapters.worker import WorkerContext
from hiveplane.llm.models import Message

#: Stable user instruction; the fake/replay provider keys on this exact prompt.
CLASSIFY_PROMPT = (
    "Classify the risk of the pull request as exactly one of: low, medium, high. "
    'Respond as JSON: {"risk": "<level>", "summary": "<one sentence>"}'
)


def _tool_text(result: Any) -> str:
    shaped = getattr(result, "shaped_output", None)
    return shaped.text if shaped is not None else "{}"


def _parse(content: str) -> tuple[str, str]:
    try:
        data = json.loads(content)
    except (ValueError, TypeError):
        text = content.strip()
        return (text.lower() or "unknown", text)
    risk = str(data.get("risk", "unknown")).strip().lower()
    summary = str(data.get("summary", "")).strip()
    return risk, summary


def run(task: dict[str, Any], ctx: WorkerContext) -> dict[str, Any]:
    """List open pull requests and classify the change risk with the model."""
    pulls = ctx.tool_call("mcp.github.list_pull_requests", host="api.github.com")
    completion = ctx.complete(
        [
            Message(role="system", content=f"Open pull requests: {_tool_text(pulls)}"),
            Message(role="system", content=f"Pull request under review: {json.dumps(task)}"),
            Message(role="user", content=CLASSIFY_PROMPT),
        ]
    )
    risk, summary = _parse(completion.content)
    return {"risk": risk, "summary": summary}
