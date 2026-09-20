"""
Generic HTTP proxy connector.

Takes a ``(base_url, HttpToolConfig)`` pair and returns an async handler
that proxies MCP tool calls to a backend REST API.  No connector-specific
Python code is needed — everything is driven by the YAML config.

GET requests pass parameters as query arguments; POST requests pass them
as a JSON body.  If ``param_mapping`` is provided, only mapped fields
are forwarded (safe default).  Without a mapping, all arguments are sent
which can leak unexpected fields to the backend.
"""

import json
import logging

import httpx

from mcplex.config import HttpToolConfig
from mcplex.context import CallMetadata, client_identity, current_call

logger = logging.getLogger(__name__)

BACKEND_TIMEOUT_SECONDS = 5.0


def validate_args(args: dict, schema: dict) -> tuple[bool, str]:
    """Validate *args* against the declared JSON Schema *schema*.

    Returns ``(is_valid, error_message)``.  When invalid, *error_message*
    is a human-readable string suitable for a ``-32602`` JSON-RPC error.
    """
    errors = []
    for param_name, param_schema in schema.items():
        expected_type = param_schema.get("type")
        value = args.get(param_name)
        is_required = param_schema.get("required", False)
        if value is None:
            if is_required:
                errors.append(f"{param_name!r}: required parameter missing")
            continue
        if expected_type == "integer" and not isinstance(value, int):
            errors.append(
                f"{param_name!r}: expected integer, got {type(value).__name__}"
            )
        elif expected_type == "string" and not isinstance(value, str):
            errors.append(
                f"{param_name!r}: expected string, got {type(value).__name__}"
            )
        elif expected_type == "number" and not isinstance(value, (int, float)):
            errors.append(
                f"{param_name!r}: expected number, got {type(value).__name__}"
            )
        elif expected_type == "boolean" and not isinstance(value, bool):
            errors.append(
                f"{param_name!r}: expected boolean, got {type(value).__name__}"
            )
        if "enum" in param_schema and value not in param_schema["enum"]:
            errors.append(
                f"{param_name!r}: value {value!r} not in enum {param_schema['enum']}"
            )
        if (
            "minimum" in param_schema
            and isinstance(value, (int, float))
            and value < param_schema["minimum"]
        ):
            errors.append(
                f"{param_name!r}: value {value} below minimum {param_schema['minimum']}"
            )
        if (
            "maximum" in param_schema
            and isinstance(value, (int, float))
            and value > param_schema["maximum"]
        ):
            errors.append(
                f"{param_name!r}: value {value} above maximum {param_schema['maximum']}"
            )

    unknown = set(args.keys()) - set(schema.keys())
    if unknown:
        errors.append(f"unknown parameters: {sorted(unknown)}")

    if errors:
        return False, "; ".join(errors)
    return True, ""


def make_proxy_handler(
    base_url: str,
    http_config: HttpToolConfig,
    parameters: dict | None = None,
    shared_client: httpx.AsyncClient | None = None,
):
    """Return an async handler that proxies to *base_url* + *http_config.path*.

    The returned handler accepts ``args: dict`` (the MCP tool arguments)
    and returns a JSON string.  If *parameters* is provided, arguments
    are validated against the declared JSON Schema before proxying.
    If *shared_client* is provided, it is reused for connection pooling;
    otherwise a new client is created per call.
    """

    async def handler(args: dict) -> str:
        if parameters:
            is_valid, err_msg = validate_args(args, parameters)
            if not is_valid:
                return json.dumps({"error": "Invalid arguments", "details": err_msg})

        base = base_url.rstrip("/")
        path = http_config.path.lstrip("/")
        url = f"{base}/{path}"

        headers = dict(http_config.headers or {})

        identity = client_identity.get()
        if identity:
            if identity.get("name"):
                headers["X-MCP-Client-Name"] = identity["name"]
            if identity.get("version"):
                headers["X-MCP-Client-Version"] = identity["version"]

        mapping = http_config.param_mapping
        if mapping:
            mapped = {
                api_key: args[mcp_key]
                for mcp_key, api_key in mapping.items()
                if mcp_key in args
            }
        else:
            mapped = dict(args)

        meta = CallMetadata(backend_url=url)
        current_call.set(meta)

        timeout = httpx.Timeout(BACKEND_TIMEOUT_SECONDS)
        client_ctx = shared_client or httpx.AsyncClient(
            timeout=timeout, follow_redirects=True
        )
        try:
            try:
                if http_config.method.upper() == "GET":
                    resp = await client_ctx.get(url, params=mapped, headers=headers)
                elif http_config.method.upper() == "POST":
                    resp = await client_ctx.post(url, json=mapped, headers=headers)
                else:
                    return json.dumps(
                        {"error": f"Unsupported HTTP method: {http_config.method}"}
                    )
            except httpx.TimeoutException:
                logger.warning(
                    "Backend timeout for %s (%.1fs)", url, BACKEND_TIMEOUT_SECONDS
                )
                return json.dumps(
                    {"error": f"Backend timed out after {BACKEND_TIMEOUT_SECONDS}s"}
                )
            except httpx.RequestError as e:
                logger.warning("Backend request failed for %s: %s", url, e)
                return json.dumps({"error": f"Backend unreachable: {e}"})

            meta.http_status = resp.status_code

            if resp.status_code >= 400:
                snippet = resp.text[:200]
                return json.dumps(
                    {
                        "error": f"Backend returned {resp.status_code}",
                        "detail": snippet,
                    }
                )

            content_type = resp.headers.get("content-type", "")
            try:
                json.loads(resp.text)
                return resp.text
            except (json.JSONDecodeError, ValueError):
                logger.warning(
                    "Backend returned non-JSON for %s: %.100s", url, resp.text
                )
                if "html" in content_type.lower():
                    return json.dumps({"html": resp.text[:5000]})
                return json.dumps(
                    {"content": resp.text[:5000], "content_type": content_type}
                )
        finally:
            if shared_client is None:
                await client_ctx.aclose()

    return handler
