"""``cauterule mcp`` launcher — start the MCP server on the chosen transport."""

from __future__ import annotations

from cauterule.mcp.ratelimit import TokenBucket
from cauterule.mcp.server import CauteruleMCPServer
from cauterule.store.manager import StoreManager


def launch_mcp(
    transport: str = "stdio",
    host: str = "localhost",
    port: int = 9000,
    auth_mode: str | None = None,
    auth_tokens: list[str] | None = None,
) -> None:
    """Start the Cauterule MCP server.

    Args:
        transport: Transport to use — ``"stdio"`` or ``"http"``.
        host: Host to bind when using ``"http"`` transport.
        port: Port to bind when using ``"http"`` transport.
        auth_mode: ``None`` resolves from ``cauterule.toml [mcp]``.
        auth_tokens: Extra bearer tokens (in addition to config/env).
    """
    from cauterule.config import load_config

    try:
        mcp_config = load_config().mcp
    except (OSError, ValueError):
        mcp_config = None
    resolved_mode = auth_mode or (mcp_config.auth_mode if mcp_config else "none")
    resolved_tokens = list(auth_tokens or []) + list(mcp_config.tokens if mcp_config else [])
    limiter = TokenBucket(
        capacity=mcp_config.rate_capacity if mcp_config else 60,
        refill_per_min=mcp_config.rate_refill_per_min if mcp_config else 30.0,
    )
    store = StoreManager()

    if transport == "stdio":
        server = CauteruleMCPServer(store)
        server.run_stdio()
    elif transport == "http":
        server = CauteruleMCPServer(
            store,
            host=host,
            port=port,
            auth_mode=resolved_mode,
            auth_tokens=resolved_tokens,
            rate_limiter=limiter,
        )
        server.run_http()
    else:
        msg = f"Unknown transport {transport!r}; use 'stdio' or 'http'"
        raise ValueError(msg)
