"""
Tool registry — maps tool names to handlers and serves the MCP catalog.

Tools are registered in two phases:
  1.  Config phase — tool metadata is loaded from YAML into ``ToolDef`` objects.
  2.  Handler phase — connector code registers async callables keyed by tool name.

The registry logs a warning at startup for any tool that has metadata
but no handler (misconfigured connector).
"""

import json
import logging
from collections.abc import Awaitable, Callable

from mcplex.config import Config

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Holds tool definitions and their async handlers."""

    def __init__(self, config: Config):
        self._tools: dict[str, dict] = {}
        self._handlers: dict[str, Callable[..., Awaitable[str]]] = {}
        for connector in config.connectors:
            for tool in connector.tools:
                self._tools[tool.name] = {
                    "def": tool,
                    "connector": connector.name,
                }

    def register_handler(
        self, tool_name: str, handler: Callable[..., Awaitable[str]]
    ) -> None:
        """Bind an async handler to a tool name."""
        self._handlers[tool_name] = handler

    def check_orphans(self) -> None:
        """Log a warning for every tool that has metadata but no handler."""
        for name in sorted(self._tools):
            if name not in self._handlers:
                connector = self._tools[name]["connector"]
                logger.warning(
                    "Tool %r (connector: %s) has no handler registered", name, connector
                )

    def list_tools(self) -> list[dict]:
        """Build the MCP ``tools/list`` response payload."""
        return [
            {
                "name": t["def"].name,
                "description": t["def"].description,
                "inputSchema": {
                    "type": "object",
                    "properties": t["def"].parameters,
                },
            }
            for t in self._tools.values()
        ]

    def has_handler(self, tool_name: str) -> bool:
        """Return True if a handler is registered for *tool_name*."""
        return tool_name in self._handlers

    def get_tool_schema(self, tool_name: str) -> dict | None:
        """Return the declared parameter JSON Schema for *tool_name*, or None."""
        entry = self._tools.get(tool_name)
        if entry:
            return entry["def"].parameters
        return None

    async def call_tool(self, name: str, arguments: dict) -> tuple[str, bool]:
        """Execute a tool by name and return ``(result_json, is_error)``.

        *is_error* is True when the handler failed, the tool was not found,
        or the handler returned an error payload — so the transport can set
        the MCP ``isError`` flag on the content block.
        """
        handler = self._handlers.get(name)
        if not handler:
            return json.dumps({"error": f"tool {name!r} not found"}), True
        try:
            result = await handler(arguments)
        except Exception as e:
            logger.exception("Tool %r raised an exception", name)
            return json.dumps({"error": str(e)}), True
        try:
            parsed = json.loads(result)
            is_error = isinstance(parsed, dict) and "error" in parsed
        except (json.JSONDecodeError, TypeError):
            is_error = False
        return result, is_error
