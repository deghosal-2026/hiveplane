"""EvalForge wrapper: LangGraph agent with OMLX — Azure-demos flavour.

Exposes ``build_agent(payload=None)`` for the EvalForge adapter.
Target slug: lg-azure
"""

from __future__ import annotations

import os
from typing import Any

os.environ.setdefault("OPENAI_API_KEY", "omlx-test")
os.environ.setdefault(
    "OPENAI_BASE_URL",
    os.environ.get("EVALFORGE_FIELD_ENDPOINT", "http://127.0.0.1:8000/v1"),
)

from langchain_openai import ChatOpenAI
from langgraph.checkpoint.memory import MemorySaver
from langgraph.graph import END, START, MessagesState, StateGraph
from langgraph.prebuilt import ToolNode


def calculator(expression: str) -> dict[str, Any]:
    """Evaluate a mathematical expression. Supports +, -, *, / and parentheses."""
    import ast
    import operator as op

    _safe_ops = {
        ast.Add: op.add,
        ast.Sub: op.sub,
        ast.Mult: op.mul,
        ast.Div: op.truediv,
        ast.USub: op.neg,
    }

    def _eval_node(node: ast.AST) -> float | int:
        if isinstance(node, ast.Expression):
            return _eval_node(node.body)
        if isinstance(node, ast.Constant) and isinstance(node.value, int | float):
            return node.value
        if isinstance(node, ast.BinOp) and type(node.op) in _safe_ops:
            return _safe_ops[type(node.op)](_eval_node(node.left), _eval_node(node.right))
        if isinstance(node, ast.UnaryOp) and type(node.op) in _safe_ops:
            return _safe_ops[type(node.op)](_eval_node(node.operand))
        raise ValueError(f"unsupported expression: {ast.dump(node)}")

    try:
        tree = ast.parse(expression, mode="eval")
        return {"expression": expression, "result": float(_eval_node(tree.body))}
    except Exception as exc:
        return {"expression": expression, "error": str(exc)}


def get_weather(city: str) -> dict[str, Any]:
    """Get the current weather for a city."""
    return {"city": city, "temperature": 18, "condition": "partly cloudy", "humidity": 65, "wind_speed_kmh": 12}


def web_search(query: str) -> dict[str, Any]:
    """Search a knowledge base for information."""
    return {
        "query": query,
        "results": [
            {"title": "Result 1", "snippet": f"Relevant information about {query}."},
            {"title": "Result 2", "snippet": f"More details on {query}."},
        ],
        "total": 2,
    }


_DEFAULT_TOOLS = [calculator, get_weather, web_search]


def build_agent(payload: dict[str, Any] | None = None) -> Any:
    payload = payload or {}
    model_name = os.environ.get("EVALFORGE_FIELD_MODEL", "Qwen3.5-4B-4bit")

    tools = list(_DEFAULT_TOOLS)
    model = ChatOpenAI(
        model=model_name,
        base_url=os.environ["OPENAI_BASE_URL"],
        api_key=os.environ["OPENAI_API_KEY"],
        temperature=0,
    )
    model = model.bind_tools(tools, parallel_tool_calls=False)

    tool_node = ToolNode(tools)

    def _should_continue(state: dict[str, Any]) -> str:
        messages = state["messages"]
        last_message = messages[-1]
        if not getattr(last_message, "tool_calls", None):
            return "end"
        return "continue"

    def _call_model(state: dict[str, Any]) -> dict[str, Any]:
        return {"messages": [model.invoke(state["messages"])]}

    workflow = StateGraph(MessagesState)
    workflow.add_node("agent", _call_model)
    workflow.add_node("action", tool_node)
    workflow.add_edge(START, "agent")
    workflow.add_conditional_edges("agent", _should_continue, {"continue": "action", "end": END})
    workflow.add_edge("action", "agent")

    return workflow.compile(checkpointer=MemorySaver())
