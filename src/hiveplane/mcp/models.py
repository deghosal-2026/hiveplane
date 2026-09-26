"""MCP Registry v2 models: servers, tools, versions, and call results (M44)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

from hiveplane.core.tools import ToolTrustLevel


class McpTransportKind(StrEnum):
    """The transport a server is reached over."""

    STDIO = "stdio"
    HTTP = "http"
    SSE = "sse"


class McpServerStatus(StrEnum):
    """Connection status of a registered MCP server."""

    REGISTERED = "registered"
    CONNECTED = "connected"
    UNREACHABLE = "unreachable"


class ToolStatus(StrEnum):
    """Lifecycle of a discovered tool."""

    DISCOVERED = "discovered"
    ACTIVE = "active"
    ABSENT = "absent"
    RETIRED = "retired"


class McpErrorCode(StrEnum):
    """Normalized MCP failure codes surfaced by the boundary."""

    UNREACHABLE = "tool_unreachable"
    ERROR = "tool_error"
    TIMEOUT = "tool_timeout"
    SCHEMA_MISMATCH = "schema_mismatch"


def server_fingerprint(endpoint: McpServerEndpoint) -> str:
    """Return a stable fingerprint for a server endpoint (M44-02)."""
    import hashlib

    material = "|".join([endpoint.kind.value, endpoint.target, *endpoint.args])
    return hashlib.sha256(material.encode("utf-8")).hexdigest()[:16]


class McpServerEndpoint(BaseModel):
    """How to reach an MCP server (stdio command or http/sse URL)."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    kind: McpTransportKind
    target: str = Field(min_length=1)
    args: list[str] = Field(default_factory=list)

    @property
    def fingerprint(self) -> str:
        """Return the server's stable identity fingerprint."""
        return server_fingerprint(self)


class McpToolDefinition(BaseModel):
    """A tool as reported by an MCP server's ``list_tools``."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str | None = None
    input_schema: dict[str, JsonValue] = Field(default_factory=dict)
    output_schema: dict[str, JsonValue] = Field(default_factory=dict)


class McpServerRecord(BaseModel):
    """A registered MCP server."""

    model_config = ConfigDict(extra="forbid")

    server_id: str = Field(min_length=1)
    endpoint: McpServerEndpoint
    fingerprint: str = Field(min_length=1)
    status: McpServerStatus = McpServerStatus.REGISTERED
    connected_at: AwareDatetime
    last_seen: AwareDatetime


class ToolVersion(BaseModel):
    """An append-only tool schema version."""

    model_config = ConfigDict(extra="forbid")

    tool_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    input_schema: dict[str, JsonValue] = Field(default_factory=dict)
    output_schema: dict[str, JsonValue] = Field(default_factory=dict)
    registered_at: AwareDatetime


class McpToolRecord(BaseModel):
    """A discovered and possibly onboarded MCP tool with a stable ID."""

    model_config = ConfigDict(extra="forbid")

    tool_id: str = Field(min_length=1)
    server_id: str = Field(min_length=1)
    server_fingerprint: str = Field(min_length=1)
    tool_name: str = Field(min_length=1)
    trust_level: ToolTrustLevel = ToolTrustLevel.READ_ONLY
    status: ToolStatus = ToolStatus.DISCOVERED
    current_version: int = Field(default=1, ge=1)
    description: str | None = None
    input_schema: dict[str, JsonValue] = Field(default_factory=dict)
    output_schema: dict[str, JsonValue] = Field(default_factory=dict)
    discovered_at: AwareDatetime
    onboarded_at: AwareDatetime | None = None


class McpCallResult(BaseModel):
    """The real result of an MCP ``tools/call``."""

    model_config = ConfigDict(extra="forbid")

    tool_id: str = Field(min_length=1)
    output: str | None = None
