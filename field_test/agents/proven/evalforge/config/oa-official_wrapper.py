"""EvalForge wrapper for OpenAI Agents SDK - official examples shim.

Exposes build_agent(payload=None) that creates an OpenAI Agents SDK agent powered by OMLX.
Based on examples/tools/shell.py pattern.
"""

import os

os.environ.setdefault("OPENAI_API_KEY", "omlx-test")
os.environ.setdefault(
    "OPENAI_BASE_URL",
    os.environ.get("EVALFORGE_FIELD_ENDPOINT", "http://127.0.0.1:8000/v1"),
)

MODEL = os.environ.get("EVALFORGE_FIELD_MODEL", "Qwen3.5-4B-4bit")

from agents import Agent, ModelSettings, Runner, function_tool, set_tracing_disabled

set_tracing_disabled(disabled=True)


@function_tool
def get_weather(city: str) -> str:
    """Returns the current weather for a given city."""
    return f"The weather in {city} is sunny with a temperature of 72F."


@function_tool
def get_current_time() -> str:
    """Returns the current date and time."""
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def build_agent(payload=None):
    instructions = (
        (payload or {}).get("instructions")
        or "You are a helpful assistant. You can check weather and time. Keep responses concise."
    )

    agent = Agent(
        name="EvalForge Agent",
        instructions=instructions,
        tools=[get_weather, get_current_time],
        model=MODEL,
        model_settings=ModelSettings(tool_choice="auto"),
    )

    return agent


def _run_agent_sync(agent, query=None):
    import asyncio

    query = query or "What's the weather in San Francisco and what time is it?"
    return asyncio.run(Runner.run(agent, query)).final_output
