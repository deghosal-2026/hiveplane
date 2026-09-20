"""EvalForge wrapper for CrewAI - awesome-quickstart shim.

Exposes build_agent(payload=None) that creates a single-agent CrewAI crew
with a get_weather tool, powered by the configured LLM endpoint.
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


@tool("get_weather")
def get_weather(city: str) -> str:
    """Get current weather for a city."""
    return f"The weather in {city} is sunny with a temperature of 20 degrees Celsius."


def build_agent(payload=None):
    llm = CrewLLM(
        model=f"openai/{MODEL}",
        base_url=os.environ["OPENAI_BASE_URL"],
        api_key=os.environ["OPENAI_API_KEY"],
    )

    agent = Agent(
        role="Helpful Assistant",
        goal="Answer the user's question accurately. Use the get_weather tool when asked about weather.",
        backstory="A helpful assistant who can check the weather and answer questions directly.",
        llm=llm,
        tools=[get_weather],
        verbose=True,
    )

    task = Task(
        description="{input}",
        expected_output="A clear, direct response to the user's question.",
        agent=agent,
    )

    crew = Crew(
        agents=[agent],
        tasks=[task],
        verbose=True,
    )

    return crew
