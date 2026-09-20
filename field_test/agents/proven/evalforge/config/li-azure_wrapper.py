"""EvalForge wrapper for LlamaIndex - azure-demos shim.

Exposes build_agent(payload=None) that creates a LlamaIndex ReAct agent powered by OMLX.
Based on llamaindex.py pattern.
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


def get_weather(city: str) -> str:
    """Get current weather for a city."""
    return f"The weather in {city} is sunny, 72F with a light breeze."


def web_web_search(query: str) -> str:
    """Search the web for information."""
    return f"Search results for: {query}"


def build_agent(payload=None):
    llm = OpenAI(
        model=MODEL,
        api_base=os.environ["OPENAI_BASE_URL"],
        api_key=os.environ["OPENAI_API_KEY"],
    )

    Settings.llm = llm

    tools = [
        FunctionTool.from_defaults(fn=get_weather),
        FunctionTool.from_defaults(fn=web_web_search),
    ]

    agent = ReActAgent(
        tools=tools,
        llm=llm,
        system_prompt="You are a helpful assistant. You can check the weather and search the web. Keep responses concise.",
    )

    return _SyncReActWrapper(agent)


class _SyncReActWrapper:
    """Expose a sync ``.chat(user_msg=)`` over the async workflow ReActAgent.

    LlamaIndex's workflow ``ReActAgent.run()`` needs a *running* event loop at
    call time (before the first await). The EvalForge llamaindex adapter calls
    the agent synchronously, so we drive the async workflow inside a fresh
    event loop here and return the result object (which has ``.response`` and
    ``.sources`` consumed by the adapter's trajectory/output extraction).
    """

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
