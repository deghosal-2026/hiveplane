"""EvalForge wrapper for AutoGen - azure-demos shim.

Exposes build_agent(payload=None) that creates an AutoGen agent powered by OMLX.
Based on agentframework_tools.py pattern.
"""

import os

os.environ.setdefault("OPENAI_API_KEY", "omlx-test")
os.environ.setdefault(
    "OPENAI_BASE_URL",
    os.environ.get("EVALFORGE_FIELD_ENDPOINT", "http://127.0.0.1:8000/v1"),
)

MODEL = os.environ.get("EVALFORGE_FIELD_MODEL", "Qwen3.5-4B-4bit")

import logging
from datetime import datetime

from autogen_agentchat.agents import AssistantAgent
from autogen_core.models import ModelInfo
from autogen_ext.models.openai import OpenAIChatCompletionClient

logging.basicConfig(level=logging.WARNING)
logger = logging.getLogger(__name__)


def get_current_time() -> str:
    logger.info("Getting current time")
    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_weather(city: str) -> str:
    logger.info(f"Getting weather for {city}")
    return f"The weather in {city} is sunny, 72F with a light breeze."


def calculate(expression: str) -> str:
    try:
        return str(eval(expression, {"__builtins__": {}}, {}))
    except Exception as e:
        return f"Calculation error: {e}"


def build_agent(payload=None):
    prompt = (payload or {}).get(
        "prompt",
        "You are a helpful assistant. You can check time, weather, and do calculations. Keep responses concise.",
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
        tools=[get_current_time, get_weather, calculate],
    )

    return agent


async def _run_agent(agent, query=None):
    from autogen_agentchat.ui import Console

    query = query or "What time is it and what's the weather in Tokyo?"
    result = await Console(agent.run_stream(task=query))
    return result
