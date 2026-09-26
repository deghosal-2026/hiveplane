"""The agent-as-tool registry: certified workloads exposed as tools (M30-04).

A tool id is ``agent.<workload>``. Only workloads that pass admission for the
target context are exposed; resolving or invoking an uncertified workload is
refused.
"""

from __future__ import annotations

from typing import Any, Protocol

from hiveplane.agent_tools.models import (
    TOOL_PREFIX,
    AgentTool,
    AgentToolNotFoundError,
)
from hiveplane.core.run import AdmissionContext
from hiveplane.registry.service import RegistryService
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext


class _AdmissionDecision(Protocol):
    admitted: bool
    reason: str | None


class _Registry(Protocol):
    def list_workloads(self) -> list[Any]: ...

    def check_admission(
        self, name: str, context: AdmissionContext
    ) -> _AdmissionDecision: ...


def tool_id_for(workload: str) -> str:
    """Return the agent-tool id for a workload."""
    return f"{TOOL_PREFIX}{workload}"


def workload_for(tool_id: str) -> str:
    """Return the workload named by an agent-tool id, or raise."""
    if not tool_id.startswith(TOOL_PREFIX):
        raise AgentToolNotFoundError(tool_id)
    workload = tool_id[len(TOOL_PREFIX) :]
    if not workload:
        raise AgentToolNotFoundError(tool_id)
    return workload


class AgentToolRegistry:
    """Exposes certified workloads as callable tools."""

    def __init__(self, registry: RegistryService | _Registry) -> None:
        self._registry = registry

    def resolve(self, tool_id: str) -> str:
        """Return the workload for ``tool_id`` (raises when unknown)."""
        workload = workload_for(tool_id)
        known = {record.name for record in self._registry.list_workloads()}
        if workload not in known:
            raise AgentToolNotFoundError(tool_id)
        return workload

    def list_tools(
        self, context: AdmissionContext, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[AgentTool]:
        """Return the certified workloads exposed as tools for ``context``."""
        tools: list[AgentTool] = []
        for record in self._registry.list_workloads():
            name = record.name
            if not self._registry.check_admission(name, context).admitted:
                continue
            metadata = record.manifest.metadata
            tools.append(
                AgentTool(
                    tool_id=tool_id_for(name),
                    workload=name,
                    description=metadata.description or "",
                )
            )
        tools.sort(key=lambda tool: tool.tool_id)
        return tools

    def admission(
        self, workload: str, context: AdmissionContext
    ) -> _AdmissionDecision:
        """Return the admission decision for a would-be nested workload."""
        return self._registry.check_admission(workload, context)
