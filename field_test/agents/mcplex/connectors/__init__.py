"""
Connector registration — wires tool handlers into the registry.

Native connectors (``incidentgpt``) provide built-in tool handlers for
the incident-commander suite.  They register only when no HTTP-proxy
connector already defines the same tool name (so config-driven
connectors take precedence).

HTTP-proxy connectors are created on-the-fly from YAML config entries
with ``type: http``.  No per-connector Python code is needed.
"""

import logging

from mcplex.connectors.http_proxy import make_proxy_handler
from mcplex.connectors.incidentgpt import register as register_incidentgpt

logger = logging.getLogger(__name__)


def register_all(registry, config=None, shared_client=None):
    """Register all connector handlers into *registry*.

    Parameters
    ----------
    registry : ToolRegistry
        The registry to populate.
    config : Config or None
        Parsed configuration.  If ``None``, only native connectors
        are registered (legacy fallback).
    shared_client : httpx.AsyncClient or None
        Shared HTTP client for connection pooling.  If None, each
        handler creates its own client per call.
    """
    http_tool_names = set()
    if config:
        for connector in config.connectors:
            if connector.type.value == "http" and connector.base_url:
                for tool in connector.tools:
                    if tool.http:
                        http_tool_names.add(tool.name)

    register_incidentgpt(registry, skip_names=http_tool_names)

    if config:
        for connector in config.connectors:
            if connector.type.value != "http" or not connector.base_url:
                continue
            for tool in connector.tools:
                if not tool.http:
                    continue
                if registry.has_handler(tool.name):
                    logger.warning(
                        "Tool %r defined by connector %r overrides existing handler",
                        tool.name,
                        connector.name,
                    )
                handler = make_proxy_handler(
                    connector.base_url, tool.http, tool.parameters, shared_client
                )
                registry.register_handler(tool.name, handler)
