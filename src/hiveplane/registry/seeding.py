"""Seed the tool registry from workload manifests (M23, #131).

A workload's ``tools.allow`` entries must exist in the registry before the
workload can be registered (``RegistryService._validate_tools`` raises
``UnknownToolError`` otherwise). This module derives a :class:`ToolRegistration`
for every allowed tool and registers the missing ones idempotently, so a fresh
control plane can be seeded from the example workloads with one command.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from hiveplane.core.workload import AgentWorkload
from hiveplane.registry.models import ToolRecord, ToolRegistration
from hiveplane.registry.service import RegistryService

#: The MCP namespace prefix stripped when deriving a server/name from a tool id.
_MCP_PREFIX = "mcp"


class SeedResult(BaseModel):
    """The outcome of a seeding pass: what was registered vs. already present."""

    model_config = ConfigDict(extra="forbid")

    registered: list[str] = Field(default_factory=list)
    existing: list[str] = Field(default_factory=list)


def _split_tool_id(tool_id: str) -> tuple[str, str]:
    """Return ``(mcp_server, name)`` for a tool id.

    ``mcp.github.list_pull_requests`` -> ``("github", "list_pull_requests")``;
    ``pagerduty.acknowledge`` -> ``("pagerduty", "acknowledge")``. An id with no
    separating dot uses the id for both.
    """
    parts = tool_id.split(".")
    if len(parts) >= 2 and parts[0] == _MCP_PREFIX:
        server, rest = parts[1], parts[2:]
    else:
        server, rest = parts[0], parts[1:]
    name = ".".join(rest) if rest else server
    return server, name


def derive_tool_registrations(manifest: AgentWorkload) -> list[ToolRegistration]:
    """Derive a tool registration for every ``tools.allow`` entry in a manifest."""
    registrations: list[ToolRegistration] = []
    for entry in manifest.spec.tools.allow:
        server, name = _split_tool_id(entry.tool_id)
        registrations.append(
            ToolRegistration(
                tool_id=entry.tool_id,
                name=name,
                mcp_server=server,
                trust_level=entry.trust_level,
                description=f"Seeded from workload {manifest.name!r}.",
            )
        )
    return registrations


def seed_tools(
    registry: RegistryService,
    registrations: Iterable[ToolRegistration],
    *,
    registered_by: str = "seed",
    clock: Callable[[], datetime] | None = None,
) -> SeedResult:
    """Register any missing tools; skip tools already in the registry."""
    now = (clock or (lambda: datetime.now(UTC)))()
    known = {tool.tool_id for tool in registry.list_tools()}
    result = SeedResult()
    for registration in registrations:
        if registration.tool_id in known:
            if registration.tool_id not in result.existing:
                result.existing.append(registration.tool_id)
            continue
        registry.register_tool(
            ToolRecord(
                **registration.model_dump(),
                registered_at=now,
                registered_by=registered_by,
            )
        )
        known.add(registration.tool_id)
        result.registered.append(registration.tool_id)
    return result
