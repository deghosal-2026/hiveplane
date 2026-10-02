"""Durable storage for the MCP Registry v2 (M44)."""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import Engine, delete, select

from hiveplane.config import Settings, get_settings
from hiveplane.mcp.models import McpServerRecord, McpToolRecord, ToolVersion
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import (
    McpServerRow,
    McpToolRow,
    McpToolVersionRow,
)
from hiveplane.tenancy.context import DEFAULT_CONTEXT, TenantContext
from hiveplane.tenancy.errors import TenantScopeError


class McpStore(Protocol):
    """Storage interface for MCP servers, tools, and tool versions."""

    def save_server(
        self, server: McpServerRecord, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def list_servers(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> list[McpServerRecord]: ...

    def save_tool(
        self, tool: McpToolRecord, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def get_tool(
        self, tool_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> McpToolRecord | None: ...

    def list_tools(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> list[McpToolRecord]: ...

    def save_version(
        self, version: ToolVersion, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def list_versions(
        self, tool_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[ToolVersion]: ...

    def clear(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None: ...


class InMemoryMcpStore:
    """A process-local, tenant-scoped MCP store."""

    def __init__(self) -> None:
        self._servers: dict[tuple[str, str], McpServerRecord] = {}
        self._tools: dict[tuple[str, str], McpToolRecord] = {}
        self._versions: dict[tuple[str, str, int], ToolVersion] = {}
        self._server_tenants: dict[str, str] = {}
        self._tool_tenants: dict[str, str] = {}

    def _claim(self, existing: str | None, ctx: TenantContext, label: str) -> None:
        if existing is not None and existing != ctx.tenant_id and not ctx.is_system:
            raise TenantScopeError(
                ctx.tenant_id, f"{label} belongs to another tenant"
            )

    def save_server(
        self, server: McpServerRecord, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        self._claim(self._server_tenants.get(server.server_id), ctx, f"server {server.server_id!r}")
        self._servers[(ctx.tenant_id, server.server_id)] = server.model_copy(deep=True)
        self._server_tenants[server.server_id] = ctx.tenant_id

    def list_servers(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> list[McpServerRecord]:
        return [
            record.model_copy(deep=True)
            for (tenant, _), record in sorted(self._servers.items())
            if ctx.is_system or tenant == ctx.tenant_id
        ]

    def save_tool(
        self, tool: McpToolRecord, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        self._claim(self._tool_tenants.get(tool.tool_id), ctx, f"tool {tool.tool_id!r}")
        self._tools[(ctx.tenant_id, tool.tool_id)] = tool.model_copy(deep=True)
        self._tool_tenants[tool.tool_id] = ctx.tenant_id

    def get_tool(
        self, tool_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> McpToolRecord | None:
        record = self._tools.get((ctx.tenant_id, tool_id))
        return None if record is None else record.model_copy(deep=True)

    def list_tools(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> list[McpToolRecord]:
        return [
            record.model_copy(deep=True)
            for (tenant, _), record in sorted(self._tools.items())
            if ctx.is_system or tenant == ctx.tenant_id
        ]

    def save_version(
        self, version: ToolVersion, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        self._claim(self._tool_tenants.get(version.tool_id), ctx, f"tool {version.tool_id!r}")
        self._versions[(ctx.tenant_id, version.tool_id, version.version)] = (
            version.model_copy(deep=True)
        )

    def list_versions(
        self, tool_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[ToolVersion]:
        return [
            record.model_copy(deep=True)
            for (tenant, tid, _), record in sorted(self._versions.items())
            if (ctx.is_system or tenant == ctx.tenant_id) and tid == tool_id
        ]

    def clear(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        for server_key in list(self._servers):
            if ctx.is_system or server_key[0] == ctx.tenant_id:
                del self._servers[server_key]
        for tool_key in list(self._tools):
            if ctx.is_system or tool_key[0] == ctx.tenant_id:
                del self._tools[tool_key]
        for version_key in list(self._versions):
            if ctx.is_system or version_key[0] == ctx.tenant_id:
                del self._versions[version_key]
        if ctx.is_system:
            self._server_tenants.clear()
            self._tool_tenants.clear()


class PostgresMcpStore:
    """A durable MCP store backed by PostgreSQL (M44)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def save_server(
        self, server: McpServerRecord, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        with self._session.begin() as session:
            existing = session.get(McpServerRow, server.server_id)
            if (
                existing is not None
                and existing.tenant_id != ctx.tenant_id
                and not ctx.is_system
            ):
                raise TenantScopeError(
                    ctx.tenant_id, f"server {server.server_id!r} belongs to another tenant"
                )
            session.merge(
                McpServerRow(
                    server_id=server.server_id,
                    tenant_id=ctx.tenant_id,
                    fingerprint=server.fingerprint,
                    status=server.status.value,
                    last_seen=server.last_seen,
                    payload=server.model_dump(mode="json"),
                )
            )

    def list_servers(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> list[McpServerRecord]:
        statement = select(McpServerRow).order_by(McpServerRow.server_id)
        if not ctx.is_system:
            statement = statement.where(McpServerRow.tenant_id == ctx.tenant_id)
        with self._session() as session:
            rows = session.scalars(statement).all()
        return [McpServerRecord.model_validate(row.payload) for row in rows]

    def save_tool(
        self, tool: McpToolRecord, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        with self._session.begin() as session:
            existing = session.get(McpToolRow, tool.tool_id)
            if (
                existing is not None
                and existing.tenant_id != ctx.tenant_id
                and not ctx.is_system
            ):
                raise TenantScopeError(
                    ctx.tenant_id, f"tool {tool.tool_id!r} belongs to another tenant"
                )
            session.merge(
                McpToolRow(
                    tool_id=tool.tool_id,
                    tenant_id=ctx.tenant_id,
                    server_id=tool.server_id,
                    tool_name=tool.tool_name,
                    status=tool.status.value,
                    payload=tool.model_dump(mode="json"),
                )
            )

    def get_tool(
        self, tool_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> McpToolRecord | None:
        with self._session() as session:
            row = session.get(McpToolRow, tool_id)
            if row is None or not ctx.scopes(row.tenant_id):
                return None
            return McpToolRecord.model_validate(row.payload)

    def list_tools(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> list[McpToolRecord]:
        statement = select(McpToolRow).order_by(McpToolRow.tool_id)
        if not ctx.is_system:
            statement = statement.where(McpToolRow.tenant_id == ctx.tenant_id)
        with self._session() as session:
            rows = session.scalars(statement).all()
        return [McpToolRecord.model_validate(row.payload) for row in rows]

    def save_version(
        self, version: ToolVersion, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        with self._session.begin() as session:
            owner = session.get(McpToolRow, version.tool_id)
            if owner is not None and owner.tenant_id != ctx.tenant_id and not ctx.is_system:
                raise TenantScopeError(
                    ctx.tenant_id, f"tool {version.tool_id!r} belongs to another tenant"
                )
            existing = session.scalars(
                select(McpToolVersionRow).where(
                    McpToolVersionRow.tenant_id == ctx.tenant_id,
                    McpToolVersionRow.tool_id == version.tool_id,
                    McpToolVersionRow.version == version.version,
                )
            ).first()
            if existing is not None:
                existing.payload = version.model_dump(mode="json")
                return
            session.add(
                McpToolVersionRow(
                    tenant_id=ctx.tenant_id,
                    tool_id=version.tool_id,
                    version=version.version,
                    registered_at=version.registered_at,
                    payload=version.model_dump(mode="json"),
                )
            )

    def list_versions(
        self, tool_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[ToolVersion]:
        statement = select(McpToolVersionRow).where(McpToolVersionRow.tool_id == tool_id)
        if not ctx.is_system:
            statement = statement.where(McpToolVersionRow.tenant_id == ctx.tenant_id)
        statement = statement.order_by(McpToolVersionRow.version)
        with self._session() as session:
            rows = session.scalars(statement).all()
        return [ToolVersion.model_validate(row.payload) for row in rows]

    def clear(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        with self._session.begin() as session:
            for table in (McpToolVersionRow, McpToolRow, McpServerRow):
                statement = delete(table)
                if not ctx.is_system:
                    statement = statement.where(table.tenant_id == ctx.tenant_id)
                session.execute(statement)


def build_mcp_store(settings: Settings | None = None) -> McpStore:
    """Build the configured MCP store (in-memory by default)."""
    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresMcpStore(create_engine_from_settings(resolved))
    return InMemoryMcpStore()
