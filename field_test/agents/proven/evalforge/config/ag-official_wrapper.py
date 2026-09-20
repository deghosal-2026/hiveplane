"""EvalForge wrapper for AutoGen - official examples shim.

Exposes build_agent(payload=None) that creates an AutoGen agent powered by OMLX.
"""

import os

os.environ.setdefault("OPENAI_API_KEY", "omlx-test")
os.environ.setdefault(
    "OPENAI_BASE_URL",
    os.environ.get("EVALFORGE_FIELD_ENDPOINT", "http://127.0.0.1:8000/v1"),
)

MODEL = os.environ.get("EVALFORGE_FIELD_MODEL", "Qwen3.5-4B-4bit")

from autogen_agentchat.agents import AssistantAgent
from autogen_core.models import ModelInfo
from autogen_ext.models.openai import OpenAIChatCompletionClient


def add(a: int, b: int) -> str:
    return str(a + b)


def get_current_time() -> str:
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_weather(city: str) -> str:
    """Get current weather for a city."""
    return f"The weather in {city} is sunny, 72F with a light breeze."


def build_agent(payload=None):
    prompt = (payload or {}).get(
        "prompt",
        "You are a helpful assistant. You can add numbers, check the time, and get the weather.",
    )

    model_client = OpenAIChatCompletionClient(
        model=MODEL,
        base_url=os.environ["OPENAI_BASE_URL"],
        api_key=os.environ["OPENAI_API_KEY"],
        model_info=ModelInfo(
            vision=False,
            function_calling=True,
            json_output=False,
            structured_output=False,
            family="unknown",
        ),
    )

    agent = AssistantAgent(
        name="EvalForgeAgent",
        model_client=model_client,
        system_message=prompt,
        tools=[add, get_current_time, get_weather],
    )

    return agent
