"""Container entrypoint: HTTP transport for the MCP surface (M8)."""

from __future__ import annotations

import os

from .http_serve import bootstrap_config
from .mcp_http import serve_mcp_http


def main() -> None:
    bootstrap_config()
    store = os.environ.get("PC_STORE", "/data/plans.db")
    config = os.environ.get("PC_CONFIG", "/data/plancritic.toml")
    host = os.environ.get("PC_HOST", "0.0.0.0")  # noqa: S104 - container binding to all interfaces
    port = int(os.environ.get("PC_PORT", "9090"))
    server = serve_mcp_http(store, host=host, port=port, llm_config_path=config)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        server.shutdown()


if __name__ == "__main__":  # pragma: no cover
    main()
