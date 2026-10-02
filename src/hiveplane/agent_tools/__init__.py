"""Agent-as-tool: certified workloads exposed as callable tools (M30)."""

from __future__ import annotations

from hiveplane.agent_tools.engine import DEFAULT_MAX_DEPTH, AgentToolInvoker
from hiveplane.agent_tools.models import (
    TOOL_PREFIX,
    AgentTool,
    AgentToolInvocation,
    AgentToolInvocationRequest,
    AgentToolNotFoundError,
    AgentToolRefusedError,
    InvocationDecision,
)
from hiveplane.agent_tools.registry import (
    AgentToolRegistry,
    tool_id_for,
    workload_for,
)
from hiveplane.agent_tools.store import (
    AgentToolStore,
    InMemoryAgentToolStore,
    PostgresAgentToolStore,
    build_agent_tool_store,
)

__all__ = [
    "DEFAULT_MAX_DEPTH",
    "TOOL_PREFIX",
    "AgentTool",
    "AgentToolInvocation",
    "AgentToolInvocationRequest",
    "AgentToolInvoker",
    "AgentToolNotFoundError",
    "AgentToolRefusedError",
    "AgentToolRegistry",
    "AgentToolStore",
    "InMemoryAgentToolStore",
    "InvocationDecision",
    "PostgresAgentToolStore",
    "build_agent_tool_store",
    "tool_id_for",
    "workload_for",
]
