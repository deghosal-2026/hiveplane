"""Shim for sm-deepsearch (DeepSearchAgents) — smolagents adapter.

Uses the agent's ToolCallingAgent pattern with self-contained tools
that work without external API keys. Original agent has 10+ tools
requiring Serper/xAI/Jina/Wolfram API keys — those are excluded.
"""

import os
import sys

AGENT_DIR = os.path.join(os.path.dirname(__file__), "../agents/sm-deepsearch")
SRC_DIR = os.path.join(AGENT_DIR, "src")
for p in (AGENT_DIR, SRC_DIR):
    if p not in sys.path:
        sys.path.insert(0, p)


def build_agent(payload=None):
    os.environ.setdefault("OPENAI_API_KEY", "omlx-test")
    os.environ.setdefault(
        "OPENAI_BASE_URL",
        os.environ.get("EVALFORGE_FIELD_ENDPOINT", "http://127.0.0.1:8000/v1"),
    )
    model_name = os.environ.get("EVALFORGE_FIELD_MODEL", "Qwen3.5-4B-4bit")

    from smolagents import ToolCallingAgent, tool
    from smolagents import LiteLLMModel

    @tool
    def get_weather(city: str) -> str:
        """Get current weather for a city.

        Args:
            city: City name to look up weather for.

        Returns:
            Weather description string.
        """
        if "london" in city.lower():
            return "The weather in London is 15C and sunny."
        if "paris" in city.lower():
            return "The weather in Paris is 18C with light rain."
        return f"Weather data not available for {city}."

    @tool
    def web_search(query: str) -> str:
        """Search the web for information.

        Args:
            query: Search query string.

        Returns:
            Search results summary.
        """
        results = {
            "weather": "Weather data shows London is 15C sunny.",
            "paris": "Paris is currently 18C with light rain.",
        }
        for key, value in results.items():
            if key in query.lower():
                return value
        return f"No results found for: {query}"

    model = LiteLLMModel(
        model_id="openai/" + model_name,
        api_base=os.environ["OPENAI_BASE_URL"],
        api_key=os.environ["OPENAI_API_KEY"],
        temperature=0.2,
    )

    return ToolCallingAgent(
        tools=[get_weather, web_search],
        model=model,
        max_steps=6,
        name="deepsearch_smolagents",
        description="DeepSearch-style agent using smolagents with OMLX",
    )
