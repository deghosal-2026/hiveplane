"""HivePlane raw-worker shim over the downloaded exectrace agent-raw.

Bridges the deterministic support agent (``run_agent``) to the control-plane
seam: reads the issue through the tool boundary before answering, and routes
escalations through the approval-gated destructive tool so the governance
path (escalate -> approve -> resume) is exercised end to end.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import Any

from hiveplane.adapters.worker import WorkerContext
from hiveplane.core.decision import ActionClass

_ROOT = Path(__file__).resolve().parents[2]
_AGENT_RAW_ROOT = _ROOT / "field_test" / "agents" / "proven" / "exectrace" / "agent-raw"
if str(_AGENT_RAW_ROOT) not in sys.path:
    sys.path.insert(0, str(_AGENT_RAW_ROOT))

import agent as support_agent_module  # type: ignore[import-not-found]


def run(task: dict[str, Any], ctx: WorkerContext) -> dict[str, Any]:
    """Read the issue, answer through the real agent, escalate via approval."""
    query = str(task.get("query") or task.get("issue") or "unknown topic")
    account_id = str(task.get("account_id") or "ACC-001")

    ctx.tool_call("mcp.github.read_issue", host="api.github.com")
    result = support_agent_module.run_agent(query, account_id)

    if result.get("status") == "escalated":
        ctx.tool_call(
            "pagerduty.acknowledge",
            action_class=ActionClass.DESTRUCTIVE,
            host="api.pagerduty.com",
        )
    return {"status": result.get("status", "unknown"), **{
        key: value for key, value in result.items() if key != "status"
    }}