"""Build the live MCP registry from settings (M44)."""

from __future__ import annotations

from hiveplane.config import Settings, get_settings
from hiveplane.mcp.registry import McpRegistry
from hiveplane.mcp.store import build_mcp_store
from hiveplane.mcp.transport import DefaultMcpTransportFactory


def build_mcp_registry(settings: Settings | None = None) -> McpRegistry:
    """Build the configured MCP registry and load persisted state."""
    resolved = settings or get_settings()
    factory = DefaultMcpTransportFactory(timeout=resolved.mcp.timeout_seconds)
    registry = McpRegistry(factory, store=build_mcp_store(resolved))
    registry.load_from_store()
    return registry
