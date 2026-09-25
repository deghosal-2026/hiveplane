"""Example LangGraph workload: draft a docs change, gate on review, finalize.

Demonstrates the LangGraph adapter contract: nodes reach the control-plane
client through ``config['configurable']['hiveplane_ctx']``, call tools through
the boundary, invoke the model through the governed seam, checkpoint
cooperatively, and interrupt for review.

The v2 contract (D20 corpus-fixture coupling) asks the model for a coarse
``category`` label alongside the draft so the benchmark can exact-match a
model-stable value without judging prose.
"""

from __future__ import annotations

import json
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from hiveplane.checkpointing import default_checkpointer
from hiveplane.llm.models import Message

#: Stable user instruction; the fake/replay provider keys on this exact prompt.
DRAFT_PROMPT = (
    "Categorize the documentation request and draft a concise summary. "
    'Respond as JSON: {"category": "<tutorial|reference|changelog|bugfix>", '
    '"draft": "<text>"}'
)

CATEGORIES = ("tutorial", "reference", "changelog", "bugfix")


class DocsState(TypedDict, total=False):
    task: dict[str, Any]
    category: str
    draft: str
    reviewed: bool
    result: dict[str, Any]


def _normalize_category(task: dict[str, Any], category: str) -> str:
    """Prefer obvious bug-fix cues from the task over a drifted model label."""
    issue_text = " ".join(
        str(task.get(key, "")) for key in ("issue", "body") if task.get(key) is not None
    ).lower()
    if "fix" in issue_text or "bug" in issue_text:
        return "bugfix"
    return category


def _complete(ctx: Any, messages: list[Message]) -> Any:
    complete = getattr(ctx, "complete", None)
    if complete is None:
        return None
    return complete(messages)


def _parse(content: str) -> tuple[str, str]:
    """Return ``(category, draft)`` from a model completion."""
    try:
        data = json.loads(content)
    except (ValueError, TypeError):
        text = content.strip()
        return ("reference", text)
    if not isinstance(data, dict):
        return ("reference", str(data).strip())
    category = str(data.get("category", "reference")).strip().lower()
    if category not in CATEGORIES:
        category = "reference"
    draft = str(data.get("draft", content)).strip()
    return (category, draft)


def plan(state: DocsState, config: RunnableConfig) -> DocsState:
    """Read the issue through the tool boundary and draft via the model seam."""
    ctx = config["configurable"]["hiveplane_ctx"]
    ctx.tool_call("mcp.github.read_issue", host="api.github.com")
    ctx.report_usage(tool_calls=1)
    response = _complete(
        ctx,
        [
            Message(role="system", content=f"Task: {json.dumps(state.get('task', {}))}"),
            Message(role="user", content=DRAFT_PROMPT),
        ],
    )
    category, draft = (
        _parse(response.content) if response is not None else ("reference", "planned")
    )
    category = _normalize_category(state.get("task", {}), category)
    ctx.checkpoint()
    return {"category": category, "draft": draft}


def review_gate(state: DocsState, config: RunnableConfig) -> DocsState:
    """Pause the graph for human review."""
    ctx = config["configurable"]["hiveplane_ctx"]
    ctx.checkpoint()
    approved = interrupt(
        {
            "stage": "review",
            "category": state.get("category"),
            "draft": state.get("draft"),
        }
    )
    return {"reviewed": bool(approved)}


def finalize(state: DocsState) -> DocsState:
    """Return the structured draft result."""
    return {
        "result": {
            "status": "drafted",
            "category": state.get("category", ""),
            "summary": state.get("draft", ""),
        }
    }


_builder = StateGraph(DocsState)
_builder.add_node("plan", plan)
_builder.add_node("review_gate", review_gate)
_builder.add_node("finalize", finalize)
_builder.add_edge(START, "plan")
_builder.add_edge("plan", "review_gate")
_builder.add_edge("review_gate", "finalize")
_builder.add_edge("finalize", END)

graph = _builder.compile(checkpointer=default_checkpointer())
