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

    # ------------------------------------------------------------------ #
    # Servers
    # ------------------------------------------------------------------ #
    def connect(self, endpoint: McpServerEndpoint) -> McpServerRecord:
        """Connect to a server and discover its tools."""
        fingerprint = server_fingerprint(endpoint)
        server_id = f"srv-{fingerprint}"
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
        self._servers[server_id] = record
        self._transports[server_id] = transport
        self._save_server(record)
        self._discover(record, transport)
        return record

    def list_servers(self) -> list[McpServerRecord]:
        """Return all registered servers, ordered by id."""
        return sorted(self._servers.values(), key=lambda server: server.server_id)

    def refresh(self, server_id: str) -> list[McpToolRecord]:
        """Re-run discovery for a server, marking removed tools absent (M44-02)."""
        server = self._servers.get(server_id)
        if server is None:
            raise UnknownServerError(server_id)
        transport = self._transports[server_id]
        self._save_server(server)
        self._discover(server, transport)
        return self.list_tools(server_id=server_id)

    # ------------------------------------------------------------------ #
    # Tools
    # ------------------------------------------------------------------ #
    def onboard(
        self,
        tool_id: str,
        *,
        trust_level: ToolTrustLevel,
        actor: str,
    ) -> McpToolRecord:
        """Onboard a discovered tool: assign trust and make it active (M44-04)."""
        tool = self.get_tool(tool_id)
        if tool.status is ToolStatus.RETIRED:
            raise ToolNotAvailableError(tool_id, tool.status)
        now = self._clock()
        tool.status = ToolStatus.ACTIVE
        tool.trust_level = trust_level
        if tool.onboarded_at is None:
            tool.onboarded_at = now
        self._tools[tool_id] = tool
        if not self._versions.get(tool_id):
            self._versions[tool_id] = [
                ToolVersion(
                    tool_id=tool_id,
                    version=1,
                    input_schema=tool.input_schema,
                    output_schema=tool.output_schema,
                    registered_at=now,
                )
            ]
        self._save_tool(tool)
        return tool.model_copy(deep=True)

    def remove(self, tool_id: str) -> McpToolRecord:
        """Retire a tool; its id is never reused (M44-03)."""
        tool = self.get_tool(tool_id)
        tool.status = ToolStatus.RETIRED
        self._tools[tool_id] = tool
        self._save_tool(tool)
        return tool.model_copy(deep=True)

    def get_tool(self, tool_id: str) -> McpToolRecord:
        """Return a tool by id, or raise :class:`UnknownToolError`."""
        tool = self._tools.get(tool_id)
        if tool is None:
            raise UnknownToolError(tool_id)
        return tool.model_copy(deep=True)

    def list_tools(
        self,
        *,
        server_id: str | None = None,
        status: ToolStatus | None = None,
        trust_level: ToolTrustLevel | None = None,
    ) -> list[McpToolRecord]:
        """List tools, optionally filtered."""
        tools = sorted(self._tools.values(), key=lambda tool: tool.tool_id)
        if server_id is not None:
            tools = [tool for tool in tools if tool.server_id == server_id]
        if status is not None:
            tools = [tool for tool in tools if tool.status is status]
        if trust_level is not None:
            tools = [tool for tool in tools if tool.trust_level is trust_level]
        return [tool.model_copy(deep=True) for tool in tools]

    def versions(self, tool_id: str) -> list[ToolVersion]:
        """Return the append-only schema versions for a tool."""
        self.get_tool(tool_id)
        return [version.model_copy(deep=True) for version in self._versions.get(tool_id, [])]

    def is_available(self, tool_id: str) -> bool:
        """Return True only if the tool is active and callable."""
        tool = self._tools.get(tool_id)
        return tool is not None and tool.status is ToolStatus.ACTIVE

    def call(
        self, tool_id: str, arguments: dict[str, object] | None = None
    ) -> McpCallResult:
        """Call an active tool through its transport and return the real result."""
        tool = self.get_tool(tool_id)
        if tool.status is not ToolStatus.ACTIVE:
            raise ToolNotAvailableError(tool_id, tool.status)
        transport = self._transports[tool.server_id]
        output = transport.call_tool(tool.tool_name, arguments)
        return McpCallResult(tool_id=tool_id, output=output)

    def close(self) -> None:
        """Close all live transports (best effort)."""
        for transport in self._transports.values():
            close = getattr(transport, "close", None)
            if callable(close):
                close()
        self._transports.clear()

    # ------------------------------------------------------------------ #
    # Persistence
    # ------------------------------------------------------------------ #
    def load_from_store(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        """Repopulate the in-memory catalog from the durable store (M44)."""
        if self._store is None:
            return
        self._servers = {s.server_id: s for s in self._store.list_servers(ctx=ctx)}
        self._tools = {t.tool_id: t for t in self._store.list_tools(ctx=ctx)}
        for tool in self._tools.values():
            self._identity[(tool.server_fingerprint, tool.tool_name)] = tool.tool_id
            self._versions[tool.tool_id] = self._store.list_versions(tool.tool_id, ctx=ctx)

    def _save_server(self, server: McpServerRecord) -> None:
        if self._store is not None:
            self._store.save_server(server)

    def _save_tool(self, tool: McpToolRecord) -> None:
        if self._store is None:
            return
        self._store.save_tool(tool)
        for version in self._versions.get(tool.tool_id, []):
            self._store.save_version(version)

    # ------------------------------------------------------------------ #
    # Discovery internals
    # ------------------------------------------------------------------ #
    def _discover(self, server: McpServerRecord, transport: McpTransport) -> None:
        now = self._clock()
        definitions = transport.list_tools()
        present = {definition.name for definition in definitions}
        for definition in definitions:
            key = (server.fingerprint, definition.name)
            tool_id = self._identity.get(key)
            if tool_id is None:
                tool_id = self._id_factory()
                self._identity[key] = tool_id
                self._tools[tool_id] = McpToolRecord(
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
            else:
                self._refresh_tool(self._tools[tool_id], definition, now)
            self._save_tool(self._tools[tool_id])
        for tool in self._tools.values():
            if (
                tool.server_id == server.server_id
                and tool.tool_name not in present
                and tool.status is not ToolStatus.RETIRED
            ):
                tool.status = ToolStatus.ABSENT
                self._save_tool(tool)

    def _refresh_tool(
        self, tool: McpToolRecord, definition: McpToolDefinition, now: datetime
    ) -> None:
        tool.description = definition.description
        if tool.status in (ToolStatus.DISCOVERED, ToolStatus.ABSENT):
            tool.status = ToolStatus.DISCOVERED
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
