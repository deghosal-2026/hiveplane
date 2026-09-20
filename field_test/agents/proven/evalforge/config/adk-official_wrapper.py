"""evalforge_wrapper for adk-official: general ADK agent via event generator.

Exposes ``build_agent(payload=None)`` returning a duck-typed runner whose
``.run(user_id=..., session_id=..., new_message=...)`` yields ADK-like Events.
Uses OMLX LLM to process tool calls for get_weather and search tools.
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
        author="adk_agent",
        invocation_id="inv-1",
        actions=SimpleNamespace(),
    )


def _fc(name: str, args: dict) -> SimpleNamespace:
    return SimpleNamespace(id="call_1", name=name, args=args)


def _fr(name: str, response: dict) -> SimpleNamespace:
    return SimpleNamespace(id="call_1", name=name, response=response)


def _get_weather(city: str) -> dict:
    if city.lower() in ("new york", "london", "paris", "tokyo"):
        return {
            "status": "success",
            "report": f"The weather in {city} is partly cloudy with a temperature of 22 degrees Celsius.",
        }
    return {
        "status": "error",
        "error_message": f"Weather information for '{city}' is not available.",
    }


TOOLS = [
    {
        "type": "function",
        "function": {
            "name": "get_weather",
            "description": "Get the current weather for a city.",
            "parameters": {
                "type": "object",
                "properties": {
                    "city": {
                        "type": "string",
                        "description": "The name of the city.",
                    }
                },
                "required": ["city"],
            },
        },
    },
]

_TOOL_IMPLS = {
    "get_weather": _get_weather,
}


def build_agent(payload: dict | None = None) -> object:
    """Build a duck-typed runner whose ``.run(**kw)`` yields ADK-like events."""

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

    return SimpleNamespace(run=run, name="adk_official_agent")
