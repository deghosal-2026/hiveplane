"""Shim for awesome-quickstart smolagents — OMLX/EvalForge adapter.

Wraps a simple CodeAgent with basic tools behind an OMLX-routed LiteLLMModel.
"""

import os
import sys

AGENT_DIR = os.path.join(os.path.dirname(__file__), "../agents/_awesome-quickstart/smolagents")
if AGENT_DIR not in sys.path:
    sys.path.insert(0, AGENT_DIR)


def build_agent(payload=None):
    os.environ.setdefault("OPENAI_API_KEY", "omlx-test")
    os.environ.setdefault(
        "OPENAI_BASE_URL",
        os.environ.get("EVALFORGE_FIELD_ENDPOINT", "http://127.0.0.1:8000/v1"),
    )
    model_name = os.environ.get("EVALFORGE_FIELD_MODEL", "Qwen3.5-4B-4bit")

    from smolagents import ToolCallingAgent, VisitWebpageTool, tool
    from smolagents import LiteLLMModel

    @tool
    def get_weather(location: str, celsius: bool = False) -> str:
        """Get the current weather for a location.

        Args:
            location: Location to get weather for
            celsius: Whether to return temperature in Celsius
        """
        return f"The weather in {location} is sunny, 72F with a light breeze."

    @tool
    def web_web_search(query: str) -> str:
        """Search the web for information.

        Args:
            query: The search query
        """
        return f"Search results for: {query}"

    model = LiteLLMModel(
        model_id="openai/" + model_name,
        api_base=os.environ["OPENAI_BASE_URL"],
        api_key=os.environ["OPENAI_API_KEY"],
        temperature=0.2,
    )

    return ToolCallingAgent(
        tools=[get_weather, web_web_search, VisitWebpageTool()],
        model=model,
        max_steps=6,
    )
