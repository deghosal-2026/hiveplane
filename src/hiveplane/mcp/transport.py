"""Live MCP transport layer and fixture transports (M44-01)."""

from __future__ import annotations

import json
import select
import subprocess
import urllib.error
import urllib.request
from typing import Any, Protocol

from hiveplane.mcp.models import (
    McpErrorCode,
    McpServerEndpoint,
    McpToolDefinition,
    McpTransportKind,
)


class McpTransportError(Exception):
    """A normalized MCP transport failure."""

    def __init__(self, code: McpErrorCode, message: str) -> None:
        super().__init__(message)
        self.code = code
        self.message = message


class McpTransport(Protocol):
    """An MCP client connection to one server."""

    def list_tools(self) -> list[McpToolDefinition]: ...

    def call_tool(self, name: str, arguments: dict[str, object] | None = None) -> str: ...


class McpTransportFactory(Protocol):
    """Builds a transport for a server endpoint."""

    def create(self, endpoint: McpServerEndpoint) -> McpTransport: ...


class FixtureMcpTransport:
    """A programmable in-memory MCP server for tests and CI fixtures."""

    def __init__(
        self,
        *,
        tools: list[McpToolDefinition] | None = None,
        outputs: dict[str, str] | None = None,
    ) -> None:
        self._tools = list(tools or [])
        self._outputs = dict(outputs or {})
        self._failure: McpTransportError | None = None
        self._connected = True

    def set_tools(self, tools: list[McpToolDefinition]) -> None:
        """Replace the advertised tool set (simulates server-side change)."""
        self._tools = list(tools)

    def set_output(self, name: str, value: str) -> None:
        """Set the output returned by a tool."""
        self._outputs[name] = value

    def fail_next(self, code: McpErrorCode, message: str) -> None:
        """Queue a transport failure for the next call."""
        self._failure = McpTransportError(code, message)

    def reset_connection(self) -> None:
        """Simulate a server restart (reconnect with the same identity)."""
        self._connected = True

    def list_tools(self) -> list[McpToolDefinition]:
        self._raise_if_disconnected()
        return [tool.model_copy(deep=True) for tool in self._tools]

    def call_tool(self, name: str, arguments: dict[str, object] | None = None) -> str:
        self._raise_if_disconnected()
        if self._failure is not None:
            failure, self._failure = self._failure, None
            raise failure
        if name not in self._outputs:
            raise McpTransportError(
                McpErrorCode.ERROR, f"tool {name!r} returned no fixture output"
            )
        return self._outputs[name]

    def _raise_if_disconnected(self) -> None:
        if not self._connected:
            raise McpTransportError(McpErrorCode.UNREACHABLE, "server is unreachable")


class FixtureMcpTransportFactory:
    """Returns fixture transports keyed by server fingerprint."""

    def __init__(self, transports: dict[str, FixtureMcpTransport]) -> None:
        self._transports = dict(transports)

    def create(self, endpoint: McpServerEndpoint) -> McpTransport:
        transport = self._transports.get(endpoint.fingerprint)
        if transport is None:
            raise McpTransportError(
                McpErrorCode.UNREACHABLE,
                f"no fixture server for endpoint {endpoint.target!r}",
            )
        return transport


class McpStdioTransport:
    """A live MCP client over a stdio server subprocess (M44-01)."""

    def __init__(self, endpoint: McpServerEndpoint, *, timeout: float = 30.0) -> None:
        if endpoint.kind is not McpTransportKind.STDIO:
            raise McpTransportError(
                McpErrorCode.SCHEMA_MISMATCH, "stdio transport requires a stdio endpoint"
            )
        self._timeout = timeout
        try:
            self._proc = subprocess.Popen(
                [endpoint.target, *endpoint.args],
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                text=True,
                bufsize=1,
            )
        except OSError as exc:
            raise McpTransportError(McpErrorCode.UNREACHABLE, str(exc)) from exc
        self._counter = 0
        self._request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})

    def list_tools(self) -> list[McpToolDefinition]:
        result = self._request("tools/list", {})
        tools = result.get("tools", []) if isinstance(result, dict) else []
        return [
            McpToolDefinition(
                name=tool["name"],
                description=tool.get("description"),
                input_schema=tool.get("inputSchema", {}),
                output_schema=tool.get("outputSchema", {}),
            )
            for tool in tools
        ]

    def call_tool(self, name: str, arguments: dict[str, object] | None = None) -> str:
        result = self._request(
            "tools/call", {"name": name, "arguments": arguments or {}}
        )
        return _text_content(result)

    def close(self) -> None:
        """Terminate the server subprocess."""
        if self._proc.poll() is None:
            self._proc.terminate()
            try:
                self._proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                self._proc.kill()

    def _request(self, method: str, params: dict[str, object]) -> Any:
        if self._proc.poll() is not None:
            raise McpTransportError(McpErrorCode.UNREACHABLE, "server process has exited")
        self._counter += 1
        request = {"jsonrpc": "2.0", "id": self._counter, "method": method, "params": params}
        assert self._proc.stdin is not None
        try:
            self._proc.stdin.write(json.dumps(request) + "\n")
            self._proc.stdin.flush()
        except (BrokenPipeError, ValueError) as exc:
            raise McpTransportError(McpErrorCode.UNREACHABLE, str(exc)) from exc
        response = self._read_response()
        if "error" in response:
            message = str(response["error"].get("message", "mcp error"))
            raise McpTransportError(McpErrorCode.ERROR, message)
        return response.get("result")

    def _read_response(self) -> dict[str, Any]:
        assert self._proc.stdout is not None
        ready, _, _ = select.select([self._proc.stdout], [], [], self._timeout)
        if not ready:
            raise McpTransportError(McpErrorCode.TIMEOUT, "timed out waiting for server")
        line = self._proc.stdout.readline()
        if not line:
            raise McpTransportError(McpErrorCode.UNREACHABLE, "server closed the stream")
        parsed = json.loads(line)
        if not isinstance(parsed, dict):
            return {}
        return parsed


class McpHttpTransport:
    """A live MCP client over streamable HTTP JSON-RPC (M44-01)."""

    def __init__(self, endpoint: McpServerEndpoint, *, timeout: float = 30.0) -> None:
        if endpoint.kind is not McpTransportKind.HTTP:
            raise McpTransportError(
                McpErrorCode.SCHEMA_MISMATCH, "http transport requires an http endpoint"
            )
        self._url = endpoint.target
        self._timeout = timeout
        self._counter = 0
        self._request("initialize", {"protocolVersion": "2025-06-18", "capabilities": {}})

    def list_tools(self) -> list[McpToolDefinition]:
        result = self._request("tools/list", {})
        tools = result.get("tools", []) if isinstance(result, dict) else []
        return [
            McpToolDefinition(
                name=tool["name"],
                description=tool.get("description"),
                input_schema=tool.get("inputSchema", {}),
                output_schema=tool.get("outputSchema", {}),
            )
            for tool in tools
        ]

    def call_tool(self, name: str, arguments: dict[str, object] | None = None) -> str:
        result = self._request("tools/call", {"name": name, "arguments": arguments or {}})
        return _text_content(result)

    def _request(self, method: str, params: dict[str, object]) -> Any:
        self._counter += 1
        request = {"jsonrpc": "2.0", "id": self._counter, "method": method, "params": params}
        payload = json.dumps(request).encode("utf-8")
        http_request = urllib.request.Request(
            self._url,
            data=payload,
            headers={"Content-Type": "application/json", "Accept": "application/json"},
            method="POST",
        )
        try:
            with urllib.request.urlopen(http_request, timeout=self._timeout) as response:
                body = json.loads(response.read().decode("utf-8"))
        except urllib.error.HTTPError as exc:
            raise McpTransportError(McpErrorCode.ERROR, f"HTTP {exc.code}") from exc
        except (urllib.error.URLError, TimeoutError) as exc:
            raise McpTransportError(McpErrorCode.UNREACHABLE, str(exc)) from exc
        if "error" in body:
            message = str(body["error"].get("message", "mcp error"))
            raise McpTransportError(McpErrorCode.ERROR, message)
        return body.get("result")


class DefaultMcpTransportFactory:
    """Builds live transports for stdio and HTTP endpoints (M44-01)."""

    def __init__(self, *, timeout: float = 30.0) -> None:
        self._timeout = timeout

    def create(self, endpoint: McpServerEndpoint) -> McpTransport:
        if endpoint.kind is McpTransportKind.STDIO:
            return McpStdioTransport(endpoint, timeout=self._timeout)
        if endpoint.kind is McpTransportKind.HTTP:
            return McpHttpTransport(endpoint, timeout=self._timeout)
        raise McpTransportError(
            McpErrorCode.UNREACHABLE,
            f"{endpoint.kind.value} transport is not supported yet",
        )


def _text_content(result: Any) -> str:
    if isinstance(result, dict):
        content = result.get("content")
        if isinstance(content, list):
            texts = [
                part.get("text", "")
                for part in content
                if isinstance(part, dict) and part.get("type") == "text"
            ]
            if texts:
                return "".join(texts)
        if isinstance(result.get("structuredContent"), dict):
            return json.dumps(result["structuredContent"])
    return json.dumps(result)
