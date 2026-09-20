"""Agent adapters for invoking agents across runtimes (subprocess, import, HTTP, frameworks).

An adapter is the single seam between EvalForge and "your agent". It turns a
scenario (input + tools + context) into a normalized `RunArtifact` --
final output plus trajectory, cost, and error metadata (spec §"Agent Adapter
Contract").

Exports:
    Adapter: Abstract base class for all adapters.
    ADKAdapter: Invokes Google ADK agents via Python import.
    AutoGenAdapter: Invokes AutoGen agents via Python import.
    ClaudeAgentSDKAdapter: Invokes Claude Agent SDK agents via Python import.
    CrewAIAdapter: Invokes CrewAI agents via Python import.
    IsolatedAdapter: Runs agents in isolated subprocesses.
    LangGraphAdapter: Invokes LangGraph agents via Python import.
    LlamaIndexAdapter: Invokes LlamaIndex agents via Python import.
    OpenAIAgentsAdapter: Invokes OpenAI Agents SDK agents via Python import.
    PydanticAIAdapter: Invokes PydanticAI agents via Python import.
    SmolagentsAdapter: Invokes smolagents agents via Python import.
    build_invocation_payload: Builds the restricted payload sent to agents.
    create_adapter: Factory function to resolve an adapter from config.
    parse_agent_stdout: Parses agent stdout as a JSON envelope.
"""

from evalforge.adapters.adk import ADKAdapter
from evalforge.adapters.autogen import AutoGenAdapter
from evalforge.adapters.base import Adapter, build_invocation_payload, parse_agent_stdout
from evalforge.adapters.claude import ClaudeAgentSDKAdapter
from evalforge.adapters.crewai import CrewAIAdapter
from evalforge.adapters.factory import create_adapter
from evalforge.adapters.isolated import IsolatedAdapter
from evalforge.adapters.langgraph import LangGraphAdapter
from evalforge.adapters.llamaindex import LlamaIndexAdapter
from evalforge.adapters.openai_agents import OpenAIAgentsAdapter
from evalforge.adapters.pydantic_ai import PydanticAIAdapter
from evalforge.adapters.smolagents import SmolagentsAdapter

__all__ = [
    "ADKAdapter",
    "Adapter",
    "AutoGenAdapter",
    "ClaudeAgentSDKAdapter",
    "CrewAIAdapter",
    "IsolatedAdapter",
    "LangGraphAdapter",
    "LlamaIndexAdapter",
    "OpenAIAgentsAdapter",
    "PydanticAIAdapter",
    "SmolagentsAdapter",
    "build_invocation_payload",
    "create_adapter",
    "parse_agent_stdout",
]
