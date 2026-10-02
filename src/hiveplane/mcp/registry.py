"""The live MCP registry: discovery, stable IDs, onboarding, and calls (M44)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane.core.tools import ToolTrustLevel
from hiveplane.mcp.ids import new_tool_id
from hiveplane.mcp.models import (
    McpCallResult,
    McpServerEndpoint,
    McpServerRecord,
    McpServerStatus,
    McpToolDefinition,
    McpToolRecord,
    McpTransportKind,
    ToolStatus,
    ToolVersion,
    server_fingerprint,
)
from hiveplane.mcp.store import McpStore
from hiveplane.mcp.transport import McpTransport, McpTransportFactory
from hiveplane.tenancy.context import DEFAULT_CONTEXT, TenantContext
from hiveplane.tenancy.errors import TenantScopeError


class McpError(Exception):
    """Base class for MCP registry errors."""


class UnknownToolError(McpError):
    """Raised when a tool id is not registered."""

    def __init__(self, tool_id: str) -> None:
        super().__init__(f"unknown tool {tool_id!r}")
        self.tool_id = tool_id


class UnknownServerError(McpError):
    """Raised when a server id is not registered."""

    def __init__(self, server_id: str) -> None:
        super().__init__(f"unknown server {server_id!r}")
        self.server_id = server_id


class ToolNotAvailableError(McpError):
    """Raised when a tool is called before it is active."""

    def __init__(self, tool_id: str, status: ToolStatus) -> None:
        super().__init__(f"tool {tool_id!r} is {status.value}")
        self.tool_id = tool_id
        self.status = status


class McpRegistry:
    """A catalog of MCP servers and tools with stable identity (M44)."""

    def __init__(
        self,
        factory: McpTransportFactory,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] = new_tool_id,
        store: McpStore | None = None,
    ) -> None:
        self._factory = factory
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory
        self._store = store
        self._servers: dict[str, McpServerRecord] = {}
        self._tools: dict[str, McpToolRecord] = {}
        self._versions: dict[str, list[ToolVersion]] = {}
        self._identity: dict[tuple[str, str], str] = {}
        self._transports: dict[str, McpTransport] = {}
        self._server_owners: dict[str, str] = {}
        self._tool_owners: dict[str, str] = {}

    # ------------------------------------------------------------------ #
    # Tenancy helpers
    # ------------------------------------------------------------------ #
    def _owns(self, identifier: str, owners: dict[str, str], ctx: TenantContext) -> bool:
        """Return True when ``ctx`` may see the record owned by ``identifier``."""
        return ctx.is_system or owners.get(identifier) == ctx.tenant_id

    def _require_owner(
        self, identifier: str, owners: dict[str, str], ctx: TenantContext, kind: str
    ) -> None:
        """Deny an operation on a record owned by another tenant (fail-closed)."""
        owner = owners.get(identifier)
        if owner is not None and owner != ctx.tenant_id and not ctx.is_system:
            raise TenantScopeError(
                ctx.tenant_id, f"{kind} {identifier!r} belongs to another tenant"
            )

    # ------------------------------------------------------------------ #
    # Servers
    # ------------------------------------------------------------------ #
    def connect(
        self,
        endpoint: McpServerEndpoint,
        *,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> McpServerRecord:
        """Connect to a server and discover its tools."""
        fingerprint = server_fingerprint(endpoint)
        server_id = f"srv-{fingerprint}"
        self._require_owner(server_id, self._server_owners, ctx, "server")
        transport = self._factory.create(endpoint)
        now = self._clock()
        existing = self._servers.get(server_id)
        record = McpServerRecord(
            server_id=server_id,
            endpoint=endpoint,
            fingerprint=fingerprint,
            status=McpServerStatus.CONNECTED,
            connected_at=existing.connected_at if existing is not None else now,
            last_seen=now,
        )
        self._save_server(record, ctx)
        self._servers[server_id] = record
        self._server_owners[server_id] = ctx.tenant_id
        self._transports[server_id] = transport
        self._discover(record, transport, ctx)
        return record

    def list_servers(
        self, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[McpServerRecord]:
        """Return the acting tenant's registered servers, ordered by id."""
        servers = [
            server
            for server_id, server in self._servers.items()
            if self._owns(server_id, self._server_owners, ctx)
        ]
        return sorted(servers, key=lambda server: server.server_id)

    def refresh(
        self, server_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[McpToolRecord]:
        """Re-run discovery for a server, marking removed tools absent (M44-02)."""
        self._require_owner(server_id, self._server_owners, ctx, "server")
        server = self._servers.get(server_id)
        if server is None:
            raise UnknownServerError(server_id)
        transport = self._transport_for(server_id)
        self._save_server(server, ctx)
        self._discover(server, transport, ctx)
        return self.list_tools(server_id=server_id, ctx=ctx)

    # ------------------------------------------------------------------ #
    # Tools
    # ------------------------------------------------------------------ #
    def onboard(
        self,
        tool_id: str,
        *,
        trust_level: ToolTrustLevel,
        actor: str,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> McpToolRecord:
        """Onboard a discovered tool: assign trust and make it active (M44-04)."""
        tool = self._tools.get(tool_id)
        if tool is None:
            raise UnknownToolError(tool_id)
        self._require_owner(tool_id, self._tool_owners, ctx, "tool")
        if tool.status is ToolStatus.RETIRED:
            raise ToolNotAvailableError(tool_id, tool.status)
        now = self._clock()
        updated = tool.model_copy(deep=True)
        updated.status = ToolStatus.ACTIVE
        updated.trust_level = trust_level
        if updated.onboarded_at is None:
            updated.onboarded_at = now
        versions = self._versions.get(tool_id)
        if not versions:
            versions = [
                ToolVersion(
                    tool_id=tool_id,
                    version=1,
                    input_schema=updated.input_schema,
                    output_schema=updated.output_schema,
                    registered_at=now,
                )
            ]
        self._save_tool(updated, ctx, versions=versions)
        self._tools[tool_id] = updated
        self._tool_owners[tool_id] = ctx.tenant_id
        self._versions[tool_id] = versions
        return updated.model_copy(deep=True)

    def remove(
        self, tool_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> McpToolRecord:
        """Retire a tool; its id is never reused (M44-03)."""
        tool = self._tools.get(tool_id)
        if tool is None:
            raise UnknownToolError(tool_id)
        self._require_owner(tool_id, self._tool_owners, ctx, "tool")
        updated = tool.model_copy(deep=True)
        updated.status = ToolStatus.RETIRED
        self._save_tool(updated, ctx, versions=self._versions.get(tool_id, []))
        self._tools[tool_id] = updated
        self._tool_owners[tool_id] = ctx.tenant_id
        return updated.model_copy(deep=True)

    def get_tool(
        self, tool_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> McpToolRecord:
        """Return a tool by id within the acting tenant, or raise."""
        tool = self._tools.get(tool_id)
        if tool is None or not self._owns(tool_id, self._tool_owners, ctx):
            raise UnknownToolError(tool_id)
        return tool.model_copy(deep=True)

    def list_tools(
        self,
        *,
        server_id: str | None = None,
        status: ToolStatus | None = None,
        trust_level: ToolTrustLevel | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[McpToolRecord]:
        """List the acting tenant's tools, optionally filtered."""
        tools = sorted(
            (
                tool
                for tool_id, tool in self._tools.items()
                if self._owns(tool_id, self._tool_owners, ctx)
            ),
            key=lambda tool: tool.tool_id,
        )
        if server_id is not None:
            tools = [tool for tool in tools if tool.server_id == server_id]
        if status is not None:
            tools = [tool for tool in tools if tool.status is status]
        if trust_level is not None:
            tools = [tool for tool in tools if tool.trust_level is trust_level]
        return [tool.model_copy(deep=True) for tool in tools]

    def versions(
        self, tool_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[ToolVersion]:
        """Return the append-only schema versions for a tool."""
        self.get_tool(tool_id, ctx=ctx)
        return [version.model_copy(deep=True) for version in self._versions.get(tool_id, [])]

    def is_available(
        self, tool_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> bool:
        """Return True only if the tool is active and callable for the tenant."""
        tool = self._tools.get(tool_id)
        return (
            tool is not None
            and self._owns(tool_id, self._tool_owners, ctx)
            and tool.status is ToolStatus.ACTIVE
        )

    def call(
        self,
        tool_id: str,
        arguments: dict[str, object] | None = None,
        *,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> McpCallResult:
        """Call an active tool through its transport and return the real result."""
        tool = self.get_tool(tool_id, ctx=ctx)
        if tool.status is not ToolStatus.ACTIVE:
            raise ToolNotAvailableError(tool_id, tool.status)
        transport = self._transport_for(tool.server_id)
        output = transport.call_tool(tool.tool_name, arguments)
        return McpCallResult(tool_id=tool_id, output=output)

    def close(self) -> None:
        """Close all live transports (best effort)."""
        for transport in self._transports.values():
            close = getattr(transport, "close", None)
            if callable(close):
                close()
        self._transports.clear()

    def _transport_for(self, server_id: str) -> McpTransport:
        """Return a server's live transport, reconnecting lazily when reloaded.

        Transports are process-local and are never persisted, so a registry
        repopulated from a durable store rebuilds the connection on first use
        (M44-02); a connection failure surfaces as a normalized transport error.
        """
        transport = self._transports.get(server_id)
        if transport is not None:
            return transport
        server = self._servers.get(server_id)
        if server is None:
            raise UnknownServerError(server_id)
        transport = self._factory.create(server.endpoint)
        self._transports[server_id] = transport
        return transport

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    def load_from_store(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        """Repopulate the in-memory catalog from the durable store (M44).

        The store does not carry a tenant on the record, so loaded records are
        attributed to the acting context's tenant.
        """
        if self._store is None:
            return
        servers = self._store.list_servers(ctx=ctx)
        tools = self._store.list_tools(ctx=ctx)
        self._servers = {s.server_id: s for s in servers}
        self._server_owners = {s.server_id: ctx.tenant_id for s in servers}
        self._tools = {t.tool_id: t for t in tools}
        self._tool_owners = {t.tool_id: ctx.tenant_id for t in tools}
        for tool in self._tools.values():
            self._identity[(tool.server_fingerprint, tool.tool_name)] = tool.tool_id
            self._versions[tool.tool_id] = self._store.list_versions(tool.tool_id, ctx=ctx)

    def _save_server(
        self, server: McpServerRecord, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        if self._store is not None:
            self._store.save_server(server, ctx=ctx)

    def _save_tool(
        self,
        tool: McpToolRecord,
        ctx: TenantContext = DEFAULT_CONTEXT,
        *,
        versions: list[ToolVersion] | None = None,
    ) -> None:
        if self._store is None:
            return
        self._store.save_tool(tool, ctx=ctx)
        for version in versions if versions is not None else self._versions.get(
            tool.tool_id, []
        ):
            self._store.save_version(version, ctx=ctx)

    # ------------------------------------------------------------------ #
    # Discovery internals
    # ------------------------------------------------------------------ #
    def _discover(
        self,
        server: McpServerRecord,
        transport: McpTransport,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> None:
        now = self._clock()
        definitions = transport.list_tools()
        present = {definition.name for definition in definitions}
        for definition in definitions:
            key = (server.fingerprint, definition.name)
            tool_id = self._identity.get(key)
            if tool_id is None:
                tool_id = self._id_factory()
                record = McpToolRecord(
                    tool_id=tool_id,
                    server_id=server.server_id,
                    server_fingerprint=server.fingerprint,
                    tool_name=definition.name,
                    description=definition.description,
                    input_schema=definition.input_schema,
                    output_schema=definition.output_schema,
                    status=ToolStatus.DISCOVERED,
                    discovered_at=now,
                )
                self._save_tool(record, ctx)
                self._identity[key] = tool_id
                self._tools[tool_id] = record
                self._tool_owners[tool_id] = ctx.tenant_id
            else:
                current = self._tools[tool_id]
                updated = current.model_copy(deep=True)
                self._refresh_tool(updated, definition, now)
                self._save_tool(updated, ctx)
                self._tools[tool_id] = updated
                self._tool_owners[tool_id] = ctx.tenant_id
        for tool in list(self._tools.values()):
            if (
                tool.server_id == server.server_id
                and tool.tool_name not in present
                and tool.status is not ToolStatus.RETIRED
            ):
                updated = tool.model_copy(deep=True)
                updated.status = ToolStatus.ABSENT
                self._save_tool(updated, ctx)
                self._tools[tool.tool_id] = updated

    def _refresh_tool(
        self, tool: McpToolRecord, definition: McpToolDefinition, now: datetime
    ) -> None:
        tool.description = definition.description
        if tool.status in (ToolStatus.DISCOVERED, ToolStatus.ABSENT):
            tool.status = (
                ToolStatus.ACTIVE
                if tool.onboarded_at is not None
                else ToolStatus.DISCOVERED
            )
        if definition.input_schema != tool.input_schema or (
            definition.output_schema != tool.output_schema
        ):
            tool.current_version += 1
            self._versions.setdefault(tool.tool_id, []).append(
                ToolVersion(
                    tool_id=tool.tool_id,
                    version=tool.current_version,
                    input_schema=definition.input_schema,
                    output_schema=definition.output_schema,
                    registered_at=now,
                )
            )
        tool.input_schema = definition.input_schema
        tool.output_schema = definition.output_schema


def stdio_endpoint(command: str, *args: str) -> McpServerEndpoint:
    """Build a stdio server endpoint."""
    return McpServerEndpoint(kind=McpTransportKind.STDIO, target=command, args=list(args))


def http_endpoint(url: str) -> McpServerEndpoint:
    """Build an HTTP server endpoint."""
    return McpServerEndpoint(kind=McpTransportKind.HTTP, target=url)


def sse_endpoint(url: str) -> McpServerEndpoint:
    """Build an SSE server endpoint."""
    return McpServerEndpoint(kind=McpTransportKind.SSE, target=url)
