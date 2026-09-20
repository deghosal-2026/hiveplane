"""EvalForge wrapper for LlamaIndex - official examples shim.

Exposes build_agent(payload=None) that creates a LlamaIndex ReAct agent powered by OMLX.
"""

import os

os.environ.setdefault("OPENAI_API_KEY", "omlx-test")
os.environ.setdefault(
    "OPENAI_BASE_URL",
    os.environ.get("EVALFORGE_FIELD_ENDPOINT", "http://127.0.0.1:8000/v1"),
)

MODEL = os.environ.get("EVALFORGE_FIELD_MODEL", "Qwen3.5-4B-4bit")
if MODEL.startswith("openai/"):
    MODEL = MODEL[len("openai/"):]

from llama_index.core import Settings
from llama_index.core.agent.workflow import ReActAgent
from llama_index.core.tools import FunctionTool
from llama_index.llms.openai import OpenAI


def add(a: float, b: float) -> float:
    """Add two numbers and return the result."""
    return a + b


def get_current_time() -> str:
    """Returns the current date and time."""
    from datetime import datetime

    return datetime.now().strftime("%Y-%m-%d %H:%M:%S")


def get_weather(city: str) -> str:
    """Get current weather for a city."""
    return f"The weather in {city} is sunny with a temperature of 20 degrees Celsius."


def build_agent(payload=None):
    instructions = (payload or {}).get(
        "instructions",
        "You are a helpful assistant. You can add numbers, check the current time, and get the weather.",
    )

    llm = OpenAI(
        model=MODEL,
        api_base=os.environ["OPENAI_BASE_URL"],
        api_key=os.environ["OPENAI_API_KEY"],
    )

    Settings.llm = llm

    tools = [
        FunctionTool.from_defaults(fn=add),
        FunctionTool.from_defaults(fn=get_current_time),
        FunctionTool.from_defaults(fn=get_weather),
    ]

    agent = ReActAgent(
        tools=tools,
        llm=llm,
        system_prompt=instructions,
    )

    return _SyncReActWrapper(agent)


class _SyncReActWrapper:
    """Expose a sync ``.chat(user_msg=)`` over the async workflow ReActAgent."""

    def __init__(self, agent):
        self._agent = agent

    def chat(self, user_msg: str = ""):
        import asyncio
        from llama_index.core.agent.workflow import AgentStream
        from llama_index.core.workflow import Context

        async def _drive():
            ctx = Context(self._agent)
            handler = self._agent.run(user_msg, ctx=ctx)
            async for ev in handler.stream_events():
                if isinstance(ev, AgentStream):
                    pass
            return await handler

        try:
            return asyncio.run(_drive())
        except RuntimeError:
            loop = asyncio.new_event_loop()
            try:
                return loop.run_until_complete(_drive())
            finally:
                loop.close()
