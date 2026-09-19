"""Example raw-worker workload: triage an alert and acknowledge the incident.

Demonstrates a destructive, approval-gated tool call routed through the policy
boundary (``pagerduty.acknowledge``) alongside a read-only metrics query and a
model call through the governed seam.
"""

from __future__ import annotations

import json
from typing import Any

from hiveplane.adapters.worker import WorkerContext
from hiveplane.core.decision import ActionClass
from hiveplane.llm.models import Message

#: Stable user instruction; the fake/replay provider keys on this exact prompt.
TRIAGE_PROMPT = (
    "Triage the alert. Respond as JSON: "
    '{"severity": "<critical|warning|info>", "summary": "<one sentence>"}'
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
    severity = str(data.get("severity", "unknown")).strip().lower()
    summary = str(data.get("summary", "")).strip()
    return severity, summary


def run(task: dict[str, Any], ctx: WorkerContext) -> dict[str, Any]:
    """Query metrics, acknowledge the incident, and summarize the triage."""
    metrics = ctx.tool_call("prometheus.query")
    ctx.tool_call(
        "pagerduty.acknowledge",
        action_class=ActionClass.DESTRUCTIVE,
        host="api.pagerduty.com",
    )
    completion = ctx.complete(
        [
            Message(role="system", content=f"Alert: {json.dumps(task)}"),
            Message(role="system", content=f"Metrics: {_tool_text(metrics)}"),
            Message(role="user", content=TRIAGE_PROMPT),
        ]
    )
    severity, summary = _parse(completion.content)
    return {"severity": severity, "summary": summary}
