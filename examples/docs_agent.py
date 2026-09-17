"""Example LangGraph workload: plan a docs change, gate on review, finalize.

Demonstrates the LangGraph adapter contract: nodes reach the control-plane
client through ``config['configurable']['hiveplane_ctx']``, call tools through
the boundary, report usage, checkpoint cooperatively, and interrupt for review.
"""

from __future__ import annotations

from typing import Any, TypedDict

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph
from langgraph.types import interrupt


class DocsState(TypedDict, total=False):
    task: dict[str, Any]
    summary: str


def plan(state: DocsState, config: RunnableConfig) -> DocsState:
    """Read the issue through the tool boundary and record usage."""
    ctx = config["configurable"]["hiveplane_ctx"]
    ctx.tool_call("mcp.github.read_issue", host="api.github.com", output="{}")
    ctx.report_usage(input_tokens=80, output_tokens=30, tool_calls=1)
    ctx.checkpoint()
    return {"summary": "planned"}


def review_gate(state: DocsState, config: RunnableConfig) -> DocsState:
    """Pause the graph for human review."""
    ctx = config["configurable"]["hiveplane_ctx"]
    ctx.checkpoint()
    approved = interrupt({"stage": "review", "summary": state.get("summary")})
    return {"summary": "approved" if approved else "rejected"}


def finalize(state: DocsState) -> DocsState:
    """Finish the draft."""
    return {"summary": f"{state.get('summary', '')}+final"}


_builder = StateGraph(DocsState)
_builder.add_node("plan", plan)
_builder.add_node("review_gate", review_gate)
_builder.add_node("finalize", finalize)
_builder.add_edge(START, "plan")
_builder.add_edge("plan", "review_gate")
_builder.add_edge("review_gate", "finalize")
_builder.add_edge("finalize", END)

graph = _builder.compile(checkpointer=InMemorySaver())
