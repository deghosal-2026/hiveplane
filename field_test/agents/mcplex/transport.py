"""
MCP transport layer — JSON-RPC over HTTP and SSE.

Handles the ``/mcp`` endpoint on ``POST``.  Auto-detects Streamable HTTP
(SSE) vs plain JSON-RPC by inspecting the ``Accept`` header (case-insensitive).
All tools/call responses go through the handler which returns JSON strings;
errors are returned via the JSON-RPC ``error`` field when appropriate.
"""

import json
import logging
import time
import uuid

from starlette.requests import Request
from starlette.responses import JSONResponse, StreamingResponse

from mcplex import __version__
from mcplex.connectors.http_proxy import validate_args
from mcplex.context import client_identity, current_call
from mcplex.rate_limiter import RateLimiter
from mcplex.registry import ToolRegistry

logger = logging.getLogger(__name__)

MCP_PROTOCOL_VERSION = "2025-06-18"


def _json_rpc_response(msg_id, result=None, error=None):
    """Build a JSON-RPC 2.0 response dict."""
    body = {"jsonrpc": "2.0", "id": msg_id}
    if error:
        body["error"] = error
    else:
        body["result"] = result
    return body


def _audit_log(event: str, **fields):
    """Emit a structured audit log entry as a JSON line."""
    entry = {"event": event, "timestamp": time.time(), **fields}
    logger.info("audit %s %s", event, json.dumps(entry, default=str))


async def _handle_tools_list(registry: ToolRegistry, msg_id):
    """Return the list of all registered tools."""
    tools = registry.list_tools()
    logger.info("tools/list → %d tools", len(tools))
    return _json_rpc_response(msg_id, result={"tools": tools})


async def _handle_tools_call(
    registry: ToolRegistry,
    body: dict,
    msg_id,
    rate_limiter: RateLimiter | None = None,
    client_info: dict | None = None,
):
    """Execute a tool call and return its result."""
    start = time.monotonic()
    session_id = str(uuid.uuid4())
    params = body.get("params", {})
    name = params.get("name", "unknown")
    arguments = params.get("arguments", {})
    user_id = client_info.get("name") if client_info else None
    agent_id = user_id

    if client_info:
        client_identity.set(client_info)

    schema = registry.get_tool_schema(name)
    if schema:
        is_valid, err_msg = validate_args(arguments, schema)
        if not is_valid:
            _audit_log(
                "tool_call_validation_error",
                tool=name,
                session_id=session_id,
                user_id=user_id,
                latency_ms=0,
                error=err_msg,
            )
            return _json_rpc_response(
                msg_id,
                error={
                    "code": -32602,
                    "message": f"Invalid params: {err_msg}",
                },
            )

    if rate_limiter:
        allowed, limit_msg = rate_limiter.check(name, agent_id)
        if not allowed:
            _audit_log(
                "tool_call_rate_limited",
                tool=name,
                session_id=session_id,
                user_id=user_id,
                latency_ms=0,
            )
            result_str = json.dumps({"error": limit_msg})
            content_block: dict = {"type": "text", "text": result_str, "isError": True}
            return _json_rpc_response(msg_id, result={"content": [content_block]})

    logger.info("tools/call: %s args=%s", name, arguments)
    result_str, is_error = await registry.call_tool(name, arguments)
    latency_ms = int((time.monotonic() - start) * 1000)

    meta = current_call.get(None)
    _audit_log(
        "tool_call",
        tool=name,
        session_id=session_id,
        user_id=user_id,
        client=client_info,
        is_error=is_error,
        latency_ms=latency_ms,
        response_size_bytes=len(result_str),
        backend_url=meta.backend_url if meta else None,
        http_status=meta.http_status if meta else None,
    )

    content_block: dict = {"type": "text", "text": result_str}
    if is_error:
        content_block["isError"] = True
    return _json_rpc_response(
        msg_id,
        result={
            "content": [content_block],
        },
    )


async def _handle_request(
    body: dict,
    registry: ToolRegistry,
    rate_limiter: RateLimiter | None = None,
    client_info: dict | None = None,
):
    """Dispatch a single JSON-RPC request to the appropriate handler."""
    method = body.get("method")
    msg_id = body.get("id")

    if method == "initialize":
        params = body.get("params", {})
        client_info = params.get("clientInfo") or client_info
        logger.info("Client initialize received")
        return _json_rpc_response(
            msg_id,
            result={
                "protocolVersion": MCP_PROTOCOL_VERSION,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": "mcplex", "version": __version__},
            },
        )

    if method == "initialized":
        logger.info("Client initialized notification received")
        return None

    if method == "tools/list":
        return await _handle_tools_list(registry, msg_id)
    if method == "tools/call":
        return await _handle_tools_call(
            registry, body, msg_id, rate_limiter, client_info
        )

    return _json_rpc_response(
        msg_id,
        error={
            "code": -32601,
            "message": f"Method not found: {method}",
        },
    )


async def handle_mcp_message(
    request: Request, registry: ToolRegistry, rate_limiter: RateLimiter | None = None
):
    """Main entry point for ``/mcp`` POST requests."""
    try:
        body = await request.json()
    except Exception:
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {"code": -32700, "message": "Parse error"},
            },
            status_code=400,
        )

    if not isinstance(body, dict):
        if isinstance(body, list):
            raw_results = [
                await _handle_request(item, registry, rate_limiter, None)
                for item in body
                if isinstance(item, dict)
            ]
            results: list[dict] = [r for r in raw_results if r is not None]  # type: ignore[assignment]
            accept = request.headers.get("accept", "").lower()
            if "text/event-stream" in accept:
                return _sse_response(results)
            return JSONResponse(results)
        return JSONResponse(
            {
                "jsonrpc": "2.0",
                "id": None,
                "error": {
                    "code": -32600,
                    "message": "Invalid Request — body must be a JSON object or array",
                },
            },
            status_code=400,
        )

    try:
        response_body = await _handle_request(body, registry, rate_limiter)
    except Exception:
        logger.exception("Unhandled error in _handle_request")
        response_body = _json_rpc_response(
            body.get("id"),
            error={
                "code": -32603,
                "message": "Internal error",
            },
        )

    accept = request.headers.get("accept", "").lower()
    if "text/event-stream" in accept:
        if response_body is None:
            return _sse_response({})
        return _sse_response(response_body)
    if response_body is None:
        return JSONResponse({})
    return JSONResponse(response_body)


def _sse_response(data: dict | list) -> StreamingResponse:
    """Wrap a JSON-RPC response in an SSE ``event: message`` frame with keepalive."""
    payload = f"event: message\ndata: {json.dumps(data)}\n\n"

    async def stream():
        yield ": heartbeat\n\n"
        yield payload

    return StreamingResponse(
        stream(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
        },
    )
