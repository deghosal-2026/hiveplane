"""
MCPlex CLI entry point.

Usage::

    mcplex serve --config config.yaml --host 0.0.0.0 --port 8000
"""

import argparse
import logging
import sys

import uvicorn

from mcplex.server import create_app_from_path

logger = logging.getLogger(__name__)


def cli():
    """Entry point for the ``mcplex`` CLI (installed via pyproject.toml).

    Parses CLI args, loads the config file, and starts the uvicorn server.
    Exits with code 1 if the config file is missing or invalid.
    """
    parser = argparse.ArgumentParser(prog="mcplex")
    sub = parser.add_subparsers(dest="command", required=True)

    serve_parser = sub.add_parser("serve", help="Start the MCPlex server")
    serve_parser.add_argument(
        "--config", default="config.yaml", help="Path to config file"
    )
    serve_parser.add_argument("--host", default="0.0.0.0", help="Bind address")
    serve_parser.add_argument("--port", type=int, default=8000, help="Port")

    args = parser.parse_args()

    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )

    try:
        app = create_app_from_path(args.config)
    except FileNotFoundError:
        print(f"Error: config file not found: {args.config}", file=sys.stderr)
        sys.exit(1)
    except Exception:
        logger.exception("Failed to start MCPlex")
        sys.exit(1)

    logger.info("MCPlex starting on http://%s:%s", args.host, args.port)
    uvicorn.run(app, host=args.host, port=args.port, log_level="info")
