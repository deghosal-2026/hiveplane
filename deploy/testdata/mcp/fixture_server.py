"""A minimal fixture MCP server for live-transport tests (M44-01/08).

Speaks newline-delimited JSON-RPC 2.0 over stdio and implements ``initialize``,
``tools/list``, and ``tools/call`` for a tiny deterministic tool set.
"""

from __future__ import annotations

import json
import sys

_TOOLS = [
    {
        "name": "read_file",
        "description": "Read a file's contents.",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
    },
    {
        "name": "delete_file",
        "description": "Delete a file.",
        "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
    },
]


def _respond(request_id: object, result: object) -> None:
    sys.stdout.write(json.dumps({"jsonrpc": "2.0", "id": request_id, "result": result}))
    sys.stdout.write("\n")
    sys.stdout.flush()


def _error(request_id: object, code: int, message: str) -> None:
    payload = {"jsonrpc": "2.0", "id": request_id, "error": {"code": code, "message": message}}
    sys.stdout.write(json.dumps(payload))
    sys.stdout.write("\n")
    sys.stdout.flush()


def main() -> None:
    """Serve JSON-RPC requests until stdin closes."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        request = json.loads(line)
        method = request.get("method")
        request_id = request.get("id")
        if method == "initialize":
            _respond(request_id, {"protocolVersion": "2025-06-18", "capabilities": {}})
        elif method == "tools/list":
            _respond(request_id, {"tools": _TOOLS})
        elif method == "tools/call":
            params = request.get("params", {})
            name = params.get("name")
            arguments = params.get("arguments", {})
            if name == "read_file":
                path = arguments.get("path", "?")
                _respond(
                    request_id,
                    {"content": [{"type": "text", "text": f"contents of {path}"}]},
                )
            elif name == "delete_file":
                _respond(
                    request_id,
                    {"content": [{"type": "text", "text": "deleted"}]},
                )
            else:
                _error(request_id, -32601, f"unknown tool {name}")
        elif method in ("notifications/initialized", "notifications/cancelled"):
            continue
        else:
            _error(request_id, -32601, f"unknown method {method}")


if __name__ == "__main__":
    main()
