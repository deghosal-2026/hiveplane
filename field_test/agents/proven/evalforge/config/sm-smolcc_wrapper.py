"""Shim for sm-smolcc — smolagents adapter for OMLX/EvalForge.

Uses a ToolCallingAgent with a get_weather tool, matching the
smolagents-core scenario pack expectations.
"""

import os
import sys

AGENT_DIR = os.path.join(os.path.dirname(__file__), "../agents/sm-smolcc")
if AGENT_DIR not in sys.path:
    sys.path.insert(0, AGENT_DIR)

os.environ.setdefault("OPENAI_API_KEY", "omlx-test")
os.environ.setdefault(
    "OPENAI_BASE_URL",
    os.environ.get("EVALFORGE_FIELD_ENDPOINT", "http://127.0.0.1:8000/v1"),
)


def build_agent(payload=None):
    model_name = os.environ.get("EVALFORGE_FIELD_MODEL", "Qwen3.5-4B-4bit")

    from smolagents import ToolCallingAgent, tool
    from smolagents import LiteLLMModel

    @tool
    def get_weather(city: str) -> str:
        """Get current weather for a city.

        Args:
            city: City name to look up weather for.
        """
        return f"The weather in {city} is sunny with a temperature of 20 degrees Celsius."

    model = LiteLLMModel(
        model_id="openai/" + model_name,
        api_base=os.environ["OPENAI_BASE_URL"],
        api_key=os.environ["OPENAI_API_KEY"],
        temperature=0.2,
    )

    return ToolCallingAgent(
        tools=[get_weather],
        model=model,
        max_steps=6,
    )
