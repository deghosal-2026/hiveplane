"""MCP server module — expose CauterRule as an MCP server."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from cauterule.mcp.launch import launch_mcp
    from cauterule.mcp.server import CauteruleMCPServer

__all__ = ["CauteruleMCPServer", "launch_mcp"]


def __getattr__(name: str) -> object:
    if name == "CauteruleMCPServer":
        from cauterule.mcp.server import CauteruleMCPServer

        return CauteruleMCPServer
    if name == "launch_mcp":
        from cauterule.mcp.launch import launch_mcp

        return launch_mcp
    msg = f"module 'cauterule.mcp' has no attribute {name!r}"
    raise AttributeError(msg)
