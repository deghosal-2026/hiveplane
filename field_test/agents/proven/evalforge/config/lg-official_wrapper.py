"""EvalForge wrapper: LangGraph agent with OMLX — official LangGraph flavour.

Exposes ``build_agent(payload=None)`` for the EvalForge adapter.
Target slug: lg-official
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
from langgraph.prebuilt import create_react_agent


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

    tools_raw = payload.get("allowed_tools")
    if tools_raw:
        tools = []
        for spec in tools_raw:
            name = spec.get("name", "")
            if name == "calculator":
                tools.append(calculator)
            elif name == "get_weather":
                tools.append(get_weather)
            elif name == "search":
                tools.append(web_search)
        if not tools:
            tools = list(_DEFAULT_TOOLS)
    else:
        tools = list(_DEFAULT_TOOLS)

    model = ChatOpenAI(
        model=model_name,
        base_url=os.environ["OPENAI_BASE_URL"],
        api_key=os.environ["OPENAI_API_KEY"],
        temperature=0,
    )
    model = model.bind_tools(tools, parallel_tool_calls=False)
    return create_react_agent(model, tools=tools, prompt="You are a helpful assistant. Answer simple questions directly without using tools. Only use tools when the question requires external information like weather or search.")
