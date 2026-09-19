"""Example LangGraph workload: draft a docs change, gate on review, finalize.

Demonstrates the LangGraph adapter contract: nodes reach the control-plane
client through ``config['configurable']['hiveplane_ctx']``, call tools through
the boundary, invoke the model through the governed seam, checkpoint
cooperatively, and interrupt for review.
"""

from __future__ import annotations

import json
from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt

from hiveplane.llm.models import Message

#: Stable user instruction; the fake/replay provider keys on this exact prompt.
DRAFT_PROMPT = 'Draft a concise documentation summary. Respond as JSON: {"draft": "<text>"}'


class DocsState(TypedDict, total=False):
    task: dict[str, Any]
    draft: str
    reviewed: bool
    result: dict[str, Any]


def _complete(ctx: Any, messages: list[Message]) -> Any:
    complete = getattr(ctx, "complete", None)
    if complete is None:
        return None
    return complete(messages)


def _draft(content: str) -> str:
    try:
        data = json.loads(content)
    except (ValueError, TypeError):
        return content.strip()
    return str(data.get("draft", content)).strip()


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
    draft = _draft(response.content) if response is not None else "planned"
    ctx.checkpoint()
    return {"draft": draft}


def review_gate(state: DocsState, config: RunnableConfig) -> DocsState:
    """Pause the graph for human review."""
    ctx = config["configurable"]["hiveplane_ctx"]
    ctx.checkpoint()
    approved = interrupt({"stage": "review", "draft": state.get("draft")})
    return {"reviewed": bool(approved)}


def finalize(state: DocsState) -> DocsState:
    """Return the structured draft result."""
    return {"result": {"status": "drafted", "summary": state.get("draft", "")}}


_builder = StateGraph(DocsState)
_builder.add_node("plan", plan)
_builder.add_node("review_gate", review_gate)
_builder.add_node("finalize", finalize)
_builder.add_edge(START, "plan")
_builder.add_edge("plan", "review_gate")
_builder.add_edge("review_gate", "finalize")
_builder.add_edge("finalize", END)

graph = _builder.compile(checkpointer=InMemorySaver())
