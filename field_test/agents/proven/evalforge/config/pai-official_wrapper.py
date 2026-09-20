"""EvalForge wrapper: PydanticAI agent with OMLX — official flavour."""

from __future__ import annotations

import os
from typing import Any

os.environ.setdefault("OPENAI_API_KEY", "omlx-test")
os.environ.setdefault(
    "OPENAI_BASE_URL",
    os.environ.get("EVALFORGE_FIELD_ENDPOINT", "http://127.0.0.1:8000/v1"),
)

from openai import AsyncOpenAI
from pydantic_ai import Agent
from pydantic_ai.models.openai import OpenAIChatModel
from pydantic_ai.providers.openai import OpenAIProvider


def _calc(expression: str) -> dict[str, Any]:
    import ast, operator as op
    _ops = {ast.Add: op.add, ast.Sub: op.sub, ast.Mult: op.mul, ast.Div: op.truediv}

    def _eval(n):
        if isinstance(n, ast.Constant) and isinstance(n.value, (int, float)):
            return n.value
        if isinstance(n, ast.BinOp) and type(n.op) in _ops:
            return _ops[type(n.op)](_eval(n.left), _eval(n.right))
        raise ValueError("unsupported")

    try:
        return {"result": float(_eval(ast.parse(expression, mode="eval").body))}
    except Exception as e:
        return {"error": str(e)}


def _weather(city: str) -> dict[str, Any]:
    return {"city": city, "temperature": 18, "condition": "partly cloudy"}


def _search(query: str) -> dict[str, Any]:
    return {"results": [f"Result for: {query}"]}


def build_agent(payload: dict[str, Any] | None = None) -> Agent:
    model_name = os.environ.get("EVALFORGE_FIELD_MODEL", "gpt-4o-mini")

    client = AsyncOpenAI(
        base_url=os.environ["OPENAI_BASE_URL"],
        api_key=os.environ["OPENAI_API_KEY"],
    )
    model = OpenAIChatModel(model_name, provider=OpenAIProvider(openai_client=client))

    agent = Agent(
        model=model,
        system_prompt="You are a helpful assistant. Use tools when needed. Be concise.",
    )

    @agent.tool_plain
    def calculator(expression: str) -> dict[str, Any]:
        return _calc(expression)

    @agent.tool_plain
    def get_weather(city: str) -> dict[str, Any]:
        return _weather(city)

    @agent.tool_plain
    def web_search(query: str) -> dict[str, Any]:
        return _search(query)

    return agent
