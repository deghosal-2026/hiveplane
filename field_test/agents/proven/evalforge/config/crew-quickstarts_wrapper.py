"""EvalForge wrapper for CrewAI - crewAI-quickstarts shim.

Exposes build_agent(payload=None) that creates a CrewAI crew powered by OMLX (LiteLLM).
"""

import os

os.environ.setdefault("OPENAI_API_KEY", "omlx-test")
os.environ.setdefault(
    "OPENAI_BASE_URL",
    os.environ.get("EVALFORGE_FIELD_ENDPOINT", "http://127.0.0.1:8000/v1"),
)

MODEL = os.environ.get("EVALFORGE_FIELD_MODEL", "Qwen3.5-4B-4bit")

from crewai import Agent, Crew, Task
from crewai import LLM as CrewLLM
from crewai.tools import tool


def build_agent(payload=None):
    llm = CrewLLM(
        model=f"openai/{MODEL}",
        base_url=os.environ["OPENAI_BASE_URL"],
        api_key=os.environ["OPENAI_API_KEY"],
    )

    @tool("get_weather")
    def get_weather(city: str) -> str:
        """Get current weather for a city."""
        return f"The weather in {city} is sunny, 72F with a light breeze."

    @tool("web_web_search")
    def web_web_search(query: str) -> str:
        """Search the web for information."""
        return f"Search results for: {query}"

    assistant = Agent(
        role="Assistant",
        goal="Help the user by answering questions and using tools when needed.",
        backstory="A helpful assistant that can check weather and search the web.",
        llm=llm,
        tools=[get_weather, web_web_search],
        verbose=True,
    )

    task = Task(
        description="{input}",
        expected_output="A concise response to the user's question.",
        agent=assistant,
    )

    crew = Crew(
        agents=[assistant],
        tasks=[task],
        verbose=True,
    )

    return crew
