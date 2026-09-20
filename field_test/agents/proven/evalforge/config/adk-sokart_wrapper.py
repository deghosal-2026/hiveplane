"""evalforge_wrapper for adk-sokart: math agent via ADK-like event generator.

Exposes ``build_agent(payload=None)`` returning a duck-typed runner whose
``.run(user_id=..., session_id=..., new_message=...)`` yields ADK-like Events.
Uses OMLX LLM via ``google.adk.models.lite_llm.LiteLlm`` to process tool calls
for add, subtract, multiply, divide operations.
"""

import json
import os
from types import SimpleNamespace
from typing import Any

os.environ.setdefault("OPENAI_API_KEY", "omlx-test")
os.environ.setdefault(
    "OPENAI_BASE_URL",
    os.environ.get("EVALFORGE_FIELD_ENDPOINT", "http://127.0.0.1:8000/v1"),
)


def _make_event(
    *,
    func_calls: list | None = None,
    func_responses: list | None = None,
    final_text: str | None = None,
) -> SimpleNamespace:
    has_final = final_text is not None
    content = None
    if final_text:
        content = SimpleNamespace(parts=[SimpleNamespace(text=final_text)])
    return SimpleNamespace(
        get_function_calls=lambda: func_calls or [],
        get_function_responses=lambda: func_responses or [],
        is_final_response=lambda: has_final,
        content=content,
        author="agent_math",
        invocation_id="inv-1",
        actions=SimpleNamespace(),
    )


def _fc(name: str, args: dict) -> SimpleNamespace:
    return SimpleNamespace(id="call_1", name=name, args=args)


def _fr(name: str, response: dict) -> SimpleNamespace:
    return SimpleNamespace(id="call_1", name=name, response=response)


def _add(numbers: list[int]) -> int:
    return sum(numbers)


def _subtract(numbers: list[int]) -> int:
    if not numbers:
        return 0
    result = numbers[0]
    for num in numbers[1:]:
        result -= num
    return result


def _multiply(numbers: list[int]) -> int:
    product = 1
    for num in numbers:
        product *= num
    return product


def _divide(numbers: list[int]) -> float:
    if not numbers:
        return 0.0
    if 0 in numbers[1:]:
        raise ZeroDivisionError("Cannot divide by zero.")
    result = numbers[0]
    for num in numbers[1:]:
        result /= num
    return result


def _get_weather(city: str) -> str:
    return f"The weather in {city} is sunny with a temperature of 20 degrees Celsius."


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "add",
            "description": "Calculates the sum of a list of integers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "numbers": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "A list of integers to be added.",
                    }
                },
                "required": ["numbers"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "subtract",
            "description": "Subtracts numbers in a list sequentially from left to right.",
            "parameters": {
                "type": "object",
                "properties": {
                    "numbers": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "A list of integers to be subtracted.",
                    }
                },
                "required": ["numbers"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "multiply",
            "description": "Calculates the product of a list of integers.",
            "parameters": {
                "type": "object",
                "properties": {
                    "numbers": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "A list of integers to be multiplied.",
                    }
                },
                "required": ["numbers"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "divide",
            "description": "Divides numbers in a list sequentially from left to right.",
            "parameters": {
                "type": "object",
                "properties": {
                    "numbers": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "description": "A list of integers to be divided.",
                    }
                },
                "required": ["numbers"],
            },
        },
    },
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get current weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "The name of the city to get weather for.",
                    }
                },
                "required": ["city"],
            },
        },
    },
]

_TOOL_IMPLS = {
    "add": _add,
    "subtract": _subtract,
    "multiply": _multiply,
    "divide": _divide,
    "get_weather": _get_weather,
}


def build_agent(payload: dict | None = None) -> object:
    """Build a duck-typed runner whose ``.run(**kw)`` yields ADK-like events.

    Uses LiteLlm via the OMLX endpoint for tool-call decisions and final answers.
    """

    def run(*, user_id: str, session_id: str, new_message: object) -> Any:
        base_url = os.environ.get("OPENAI_BASE_URL", "http://127.0.0.1:8000/v1")
        api_key = os.environ.get("OPENAI_API_KEY", "omlx-test")
        model_name = os.environ.get("EVALFORGE_FIELD_MODEL", "Qwen3.5-4B-4bit")

        from openai import OpenAI
        client = OpenAI(base_url=base_url, api_key=api_key)

        msgs: list[dict] = [{"role": "user", "content": str(new_message)}]
        max_turns = 5

        for _turn in range(max_turns):
            resp = client.chat.completions.create(
                model=model_name,
                messages=msgs,
                tools=TOOLS,
                tool_choice="auto",
            )
            choice = resp.choices[0]

            if choice.message.tool_calls:
                for tc in choice.message.tool_calls:
                    fn = tc.function
                    try:
                        args = json.loads(fn.arguments) if fn.arguments else {}
                    except (ValueError, TypeError):
                        args = {}
                    yield _make_event(func_calls=[_fc(fn.name, args)])

                    impl = _TOOL_IMPLS.get(fn.name)
                    if impl:
                        try:
                            result = impl(**args)
                        except Exception as exc:
                            result = {"error": str(exc)}
                    else:
                        result = {"error": f"Unknown tool: {fn.name}"}
                    yield _make_event(func_responses=[_fr(fn.name, result)])

                    msgs.append(
                        {
                            "role": "assistant",
                            "tool_calls": [tc.model_dump()],
                        }
                    )
                    msgs.append(
                        {
                            "role": "tool",
                            "tool_call_id": tc.id,
                            "content": json.dumps(result) if isinstance(result, dict) else str(result),
                        }
                    )
            else:
                final = choice.message.content or ""
                yield _make_event(final_text=final)
                return

        yield _make_event(final_text="I was unable to compute a result.")

    return SimpleNamespace(run=run, name="adk_sokart_agent")
