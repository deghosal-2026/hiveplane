"""Execute MCP tools through the registry at the boundary (M44-06)."""

from __future__ import annotations

from hiveplane.execution.tool_executor import ToolExecutionError
from hiveplane.mcp.registry import McpRegistry
from hiveplane.mcp.transport import McpTransportError


class McpToolExecutionError(ToolExecutionError):
    """Raised when a live MCP tool call fails at the transport."""

    def __init__(self, message: str, code: str) -> None:
        super().__init__(message)
        self.code = code


class McpToolExecutor:
    """A :class:`~hiveplane.execution.tool_executor.ToolExecutor` over the live registry."""

    def __init__(self, registry: McpRegistry) -> None:
        self._registry = registry

    def execute(self, tool_id: str, arguments: dict[str, object] | None = None) -> str:
        """Call a live MCP tool and return its real output (never fabricated)."""
        try:
            result = self._registry.call(tool_id, arguments)
        except McpTransportError as exc:
            raise McpToolExecutionError(exc.message, exc.code.value) from exc
        return result.output or ""
