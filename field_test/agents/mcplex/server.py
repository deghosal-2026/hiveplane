"""
MCPlex HTTP server — Starlette application factory.

Creates the ASGI app with two routes:

  * ``/mcp`` — the MCP JSON-RPC endpoint (POST only)
  * ``/health`` — returns "ok" for health checks

Supports config hot-reload via SIGHUP: send ``kill -HUP <pid>`` to
reload ``config.yaml`` and rebuild the tool registry atomically.
"""

import logging
import signal
from contextlib import asynccontextmanager
from pathlib import Path

import httpx
from starlette.applications import Starlette
from starlette.responses import PlainTextResponse
from starlette.routing import Route

from mcplex.config import Config, load_config
from mcplex.connectors import register_all
from mcplex.rate_limiter import RateLimiter
from mcplex.registry import ToolRegistry
from mcplex.transport import handle_mcp_message

logger = logging.getLogger(__name__)


@asynccontextmanager
async def lifespan(app: Starlette):
    logger.info("MCPlex starting up")
    _install_reload_handler(app)
    yield
    client = app.state.http_client
    await client.aclose()
    logger.info("MCPlex shutting down — connections closed")


def _install_reload_handler(app: Starlette):
    """Install a SIGHUP handler that reloads the config atomically."""
    config_path = getattr(app.state, "config_path", None)
    if not config_path:
        return

    def _reload(signum, frame):
        logger.info("SIGHUP received — reloading config from %s", config_path)
        try:
            new_config = load_config(Path(config_path))
        except Exception:
            logger.exception("Config reload failed — keeping current config")
            return
        new_registry = ToolRegistry(new_config)
        new_client = httpx.AsyncClient(follow_redirects=True)
        register_all(new_registry, new_config, shared_client=new_client)
        new_registry.check_orphans()
        new_limiter = RateLimiter(
            new_config.rate_limits if new_config.rate_limits else None
        )
        app.state.registry = new_registry
        app.state.rate_limiter = new_limiter
        app.state.http_client = new_client
        logger.info(
            "Config reloaded: %d tools registered", len(new_registry.list_tools())
        )

    try:
        signal.signal(signal.SIGHUP, _reload)
    except (ValueError, OSError):
        logger.warning("SIGHUP not available on this platform — hot-reload disabled")


def create_app(
    config: Config, http_client: httpx.AsyncClient | None = None
) -> Starlette:
    """Build a fully-wired Starlette app from a parsed Config."""
    registry = ToolRegistry(config)
    shared_client = http_client or httpx.AsyncClient(follow_redirects=True)
    register_all(registry, config, shared_client=shared_client)
    registry.check_orphans()

    rate_limiter = RateLimiter(config.rate_limits if config.rate_limits else None)

    async def mcp_endpoint(request):
        return await handle_mcp_message(request, registry, rate_limiter)

    async def health_endpoint(request):
        return PlainTextResponse("ok")

    routes = [
        Route("/mcp", endpoint=mcp_endpoint, methods=["POST"]),
        Route("/health", endpoint=health_endpoint),
    ]

    app = Starlette(debug=False, routes=routes, lifespan=lifespan)
    app.state.registry = registry
    app.state.rate_limiter = rate_limiter
    app.state.http_client = shared_client
    app.state.client_identity = None
    logger.info("MCPlex ready: %d tools registered", len(registry.list_tools()))
    return app


def create_app_from_path(config_path: str) -> Starlette:
    """Convenience — load config from a file path then build the app."""
    path = Path(config_path)
    config = load_config(path)
    app = create_app(config)
    app.state.config_path = str(path)
    return app
