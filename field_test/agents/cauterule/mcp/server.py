"""MCP server scaffold — wraps CauterRule as an MCP server via FastMCP."""

from __future__ import annotations

from typing import Any

from mcp.server.fastmcp import Context, FastMCP
from mcp.types import ToolAnnotations

from cauterule.mcp.auth import McpAuthError, check_bearer, resolve_tokens
from cauterule.mcp.ratelimit import RateLimiter, TokenBucket
from cauterule.mcp.tools import get_matching_rules, get_rule, list_rules, report_failure
from cauterule.mcp.validation import McpValidationError, validate_report_failure
from cauterule.store.manager import StoreManager

_SERVER_NAME = "cauterule"
_LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})
_SERVER_INSTRUCTIONS = (
    "Expose CauterRule's standing-rule store as MCP tools. "
    "Supports matching rules to tasks, retrieving single rules with provenance, "
    "browsing the rule store, and reporting failures for extraction."
)
_TOOL_ANNOTATIONS: dict[str, ToolAnnotations] = {
    "get_matching_rules": ToolAnnotations(readOnlyHint=True),
    "get_rule": ToolAnnotations(readOnlyHint=True),
    "list_rules": ToolAnnotations(readOnlyHint=True),
    "report_failure": ToolAnnotations(readOnlyHint=False),
}


class CauteruleMCPServer:
    """MCP server that exposes Cauterule standing-rule functionality.

    Wraps an internal :class:`FastMCP` instance and registers all tools
    from :mod:`cauterule.mcp.tools` during construction.
    """

    def __init__(
        self,
        store: StoreManager,
        host: str = "localhost",
        port: int = 9000,
        auth_mode: str = "none",
        auth_tokens: list[str] | tuple[str, ...] | None = None,
        rate_limiter: RateLimiter | None = None,
    ) -> None:
        """Initialise server with a rule store and optional HTTP bind parameters.

        Remote-mode hardening (#601): ``auth_mode="bearer"`` requires an
        Authorization header, and *rate_limiter* (default TokenBucket)
        throttles per-client calls. Stdio calls (no HTTP context) skip both.
        """
        self.store = store
        self.auth_mode = auth_mode
        self.auth_tokens = resolve_tokens(list(auth_tokens or []))
        self.rate_limiter = rate_limiter or TokenBucket()
        if auth_mode == "none" and host not in _LOOPBACK_HOSTS:
            msg = (
                f"Refusing to bind MCP server to non-loopback host {host!r} with "
                "auth_mode='none'; bind to a loopback host or enable bearer auth"
            )
            raise ValueError(msg)
        self._mcp = FastMCP(
            name=_SERVER_NAME,
            instructions=_SERVER_INSTRUCTIONS,
            host=host,
            port=port,
        )
        self._register_tools()

    @staticmethod
    def _request_headers(ctx: Context[Any, Any, Any] | None) -> dict[str, str]:
        """HTTP headers from the official MCP SDK request context ({} on stdio).

        Uses ``ctx.request_context.request`` (a starlette Request when served
        over streamable-http; ``None`` on stdio) — the supported path in the
        ``mcp`` package. The previous ``fastmcp.server.dependencies`` import
        targeted a package that is not installed, so it silently returned {}
        and the guard never enforced auth (#601 regression caught by the
        v0.3.0 docker field test).
        """
        if ctx is None:
            return {}
        try:
            request = ctx.request_context.request
        except Exception:
            return {}
        if request is None:
            return {}
        return {str(k): str(v) for k, v in request.headers.items()}

    def _guard(
        self,
        ctx: Context[Any, Any, Any] | None = None,
        payload_check: str | None = None,
    ) -> tuple[str, dict[str, Any] | None]:
        """Enforce auth + rate limit (+ optional payload validation).

        Returns (client, error_response). error_response is None when allowed.
        """
        headers = self._request_headers(ctx)
        if not headers:
            return "stdio", None  # no HTTP context: local transport
        try:
            client = check_bearer(headers, self.auth_tokens, self.auth_mode)
        except McpAuthError as exc:
            return "anonymous", {"error": str(exc), "status": 401}
        retry_after = self.rate_limiter.allow(client)
        if retry_after > 0:
            return client, {
                "error": f"rate limit exceeded; retry after {retry_after:.1f}s",
                "status": 429,
                "retry_after": retry_after,
            }
        if payload_check is not None:
            try:
                validate_report_failure(payload_check)
            except McpValidationError as exc:
                return client, {
                    "error": "schema validation failed",
                    "status": 400,
                    "details": exc.errors,
                }
        return client, None

    # ------------------------------------------------------------------
    # Tool registration
    # ------------------------------------------------------------------
    def _register_tools(self) -> None:

        @self._mcp.tool(annotations=_TOOL_ANNOTATIONS["get_matching_rules"])
        def get_matching_rules_tool(
            task: str, ctx: Context[Any, Any, Any]
        ) -> list[dict[str, Any]] | dict[str, Any]:
            _, error = self._guard(ctx)
            if error is not None:
                return error
            rules = get_matching_rules(task, self.store.list_rules())
            return [r.to_dict() for r in rules]

        @self._mcp.tool(annotations=_TOOL_ANNOTATIONS["get_rule"])
        def get_rule_tool(rule_id: str, ctx: Context[Any, Any, Any]) -> dict[str, Any] | None:
            _, error = self._guard(ctx)
            if error is not None:
                return error
            rule = get_rule(rule_id, self.store)
            return rule.to_dict() if rule is not None else None

        @self._mcp.tool(annotations=_TOOL_ANNOTATIONS["list_rules"])
        def list_rules_tool(
            status: str | None = None,
            tag: str | None = None,
            ctx: Context[Any, Any, Any] | None = None,  # injected by FastMCP; None on direct call
        ) -> list[dict[str, Any]] | dict[str, Any]:
            _, error = self._guard(ctx)
            if error is not None:
                return error
            rules = list_rules(status=status, tag=tag, store=self.store)
            return [r.to_dict() for r in rules]

        @self._mcp.tool(annotations=_TOOL_ANNOTATIONS["report_failure"])
        def report_failure_tool(
            trajectory_json: str, ctx: Context[Any, Any, Any]
        ) -> dict[str, Any]:
            _, error = self._guard(ctx, payload_check=trajectory_json)
            if error is not None:
                return error
            return report_failure(trajectory_json)

    # ------------------------------------------------------------------
    # Transport runners
    # ------------------------------------------------------------------
    def run_stdio(self) -> None:
        """Run the server over stdio transport (for MCP subprocess mode)."""
        self._mcp.run(transport="stdio")

    def run_http(self, host: str = "localhost", port: int = 9000) -> None:  # noqa: ARG002
        """Run the server over streamable HTTP transport.

        The *host* and *port* are honoured when the server is constructed;
        they are accepted here for API consistency.
        """
        self._mcp.run(transport="streamable-http")
