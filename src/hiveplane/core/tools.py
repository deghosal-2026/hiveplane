"""Tool permission and MCP server models (PRD 05: tools & MCP)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class ToolTrustLevel(StrEnum):
    """Trust class of a tool; drives sandbox and approval requirements."""

    READ_ONLY = "read_only"
    DESTRUCTIVE = "destructive"


class McpServer(BaseModel):
    """An MCP server the workload may use, served by mcp-fabric."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    endpoint: str = Field(min_length=1)


class ToolRef(BaseModel):
    """A reference to a registered MCP tool with its trust level."""

    model_config = ConfigDict(extra="forbid")

    tool_id: str = Field(min_length=1)
    trust_level: ToolTrustLevel
    require_approval: bool = False
    allow_untrusted: bool = False


class ToolsSpec(BaseModel):
    """Allow/deny tool permissions. Deny wins over allow; unlisted tools are denied."""

    model_config = ConfigDict(extra="forbid")

    allow: list[ToolRef] = Field(default_factory=list)
    deny: list[str] = Field(default_factory=list)
    mcp_servers: list[McpServer] = Field(default_factory=list)
    default_trust: ToolTrustLevel = ToolTrustLevel.READ_ONLY

    def is_denied(self, tool_id: str) -> bool:
        """Return True if the tool is explicitly denied."""
        return tool_id in self.deny

    def is_allowed(self, tool_id: str) -> bool:
        """Return True only if the tool is explicitly allowed and not denied."""
        if self.is_denied(tool_id):
            return False
        return any(entry.tool_id == tool_id for entry in self.allow)

    def approval_required(self, tool_id: str) -> bool:
        """Return True if the allowed tool is marked as requiring approval."""
        return any(
            entry.tool_id == tool_id and entry.require_approval for entry in self.allow
        )

    def allows_untrusted(self, tool_id: str) -> bool:
        """Return True if the allowed tool may receive untrusted input (M39)."""
        return any(
            entry.tool_id == tool_id and entry.allow_untrusted for entry in self.allow
        )
