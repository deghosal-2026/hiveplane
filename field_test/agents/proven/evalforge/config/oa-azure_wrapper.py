"""EvalForge wrapper for OpenAI Agents SDK - azure-demos shim.

Exposes build_agent(payload=None) that creates an OpenAI Agents SDK agent powered by OMLX.
Based on openai_agents_tools.py pattern.
"""

import os

os.environ.setdefault("OPENAI_API_KEY", "omlx-test")
os.environ.setdefault(
    "OPENAI_BASE_URL",
    os.environ.get("EVALFORGE_FIELD_ENDPOINT", "http://127.0.0.1:8000/v1"),
)

MODEL = os.environ.get("EVALFORGE_FIELD_MODEL", "Qwen3.5-4B-4bit")

from openai import AsyncOpenAI
from agents import Agent, Runner, function_tool, set_tracing_disabled

set_tracing_disabled(disabled=True)


def _get_client():
    return AsyncOpenAI(
        base_url=os.environ["OPENAI_BASE_URL"],
        api_key=os.environ["OPENAI_API_KEY"],
    )


@function_tool
def get_current_time() -> str:
    """Returns the current date and time."""
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


@function_tool
def get_weather(city: str) -> str:
    """Get current weather for a city."""
    return f"The weather in {city} is sunny, 72F with a light breeze."


@function_tool
def calculate(expression: str) -> str:
    """Evaluates a mathematical expression and returns the result."""
    try:
        return str(eval(expression, {"__builtins__": {}}, {}))
    except Exception as e:
        return f"Error: {e}"


def build_agent(payload=None):
    client = _get_client()

    agent = Agent(
        name="EvalForge Assistant",
        instructions="You are a helpful assistant. You can check the time and perform calculations. Keep your responses concise and helpful.",
        tools=[get_current_time, get_weather, calculate],
        model=MODEL,
    )

    return agent


async def _run_agent(agent, query=None):
    query = query or "Hello! What time is it and what is 15 * 7?"
    result = await Runner.run(agent, query)
    return result.final_output
