"""Minimal HTTP transport for the MCP server (M8 / DD-14).

Exposes :class:`~planner_critic.server.mcp.PlannerCriticMCPServer` over
plain HTTP so container-to-container clients can call ``/tools`` and
``/rpc`` the way a real MCP client would. Stdlib only — no new dependency.
"""

from __future__ import annotations

import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any

from .mcp import PlannerCriticMCPServer, create_server


class MCPHTTPServer(ThreadingHTTPServer):
    """Threaded HTTP server wrapping :class:`PlannerCriticMCPServer`."""

    daemon_threads = True

    def __init__(self, host: str, port: int, mcp: PlannerCriticMCPServer) -> None:
        super().__init__((host, port), _MCPHandler)
        self.mcp = mcp


class _MCPHandler(BaseHTTPRequestHandler):
    """Route GET /healthz + GET /tools + POST /rpc to the MCP server."""

    def _send(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self) -> None:
        mcp = getattr(self.server, "mcp", None)
        if mcp is None:
            self._send(500, {"status": "error", "error": "mcp server not configured"})
            return
        if self.path == "/healthz":
            self._send(200, {"status": "ok"})
        elif self.path == "/tools":
            self._send(200, {"tools": mcp.list_tools()})
        else:
            self._send(404, {"status": "error", "error": f"unknown route: {self.path}"})

    def do_POST(self) -> None:
        mcp = getattr(self.server, "mcp", None)
        if mcp is None:
            self._send(500, {"status": "error", "error": "mcp server not configured"})
            return
        try:
            length = int(self.headers.get("Content-Length", 0))
            request = json.loads(self.rfile.read(length).decode())
            name = request["tool"]
            args = request.get("args", {})
            result = mcp.handle_tool(name, args)
        except Exception as exc:  # malformed JSON / unknown tool
            result = {"status": "error", "error": str(exc)}
        self._send(200, result)

    def log_message(self, format: str, *args: Any) -> None:
        return


def serve_mcp_http(
    store_path: str,
    host: str = "0.0.0.0",  # noqa: S104 - container binding to all interfaces
    port: int = 9090,
    llm_config_path: str | None = None,
) -> MCPHTTPServer:
    """Build a configured :class:`MCPHTTPServer` for a store + optional LLM config."""
    mcp = create_server(store_path, llm_config_path=llm_config_path)
    return MCPHTTPServer(host, port, mcp)


__all__ = ["MCPHTTPServer", "serve_mcp_http"]
