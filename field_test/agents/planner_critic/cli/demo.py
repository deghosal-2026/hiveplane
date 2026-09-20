"""``plancritic demo`` — run the end-to-end demo (F-86, CUJ 14).

Wraps :func:`planner_critic.demo.runner.run_demo` behind the standard
subcommand shape (``build_demo_parser`` + ``run_demo``). The default
``--goal`` is the *packaged* migration scenario (D11 §6) so the command works
after ``pip install`` without coupling to the ``examples/`` tree; the store
defaults to ``.plancritic/plans.db`` and falls back to memory when down
(side-channel contract, §7.2).
"""

from __future__ import annotations

import argparse
from importlib.resources import files
from pathlib import Path

from ..demo.runner import run_demo as run_demo_engine
from ..store.base import InMemoryStore, StoreUnavailable
from ..store.sqlite import SQLiteStore

DEFAULT_DB_PATH = ".plancritic/plans.db"


def _default_goal() -> str:
    """The packaged migration goal shipped inside ``planner_critic/demo/data``."""
    resource = files("planner_critic.demo").joinpath("data", "migration.json")
    return str(Path(str(resource)))


def build_demo_parser() -> argparse.ArgumentParser:
    """Build the ``demo`` subcommand parser."""
    parser = argparse.ArgumentParser(
        prog="plancritic demo",
        description="Run the demo story end-to-end (F-86)",
        add_help=False,
    )
    parser.add_argument("--goal", default=_default_goal(), help="Corpus Goal JSON path")
    parser.add_argument("--store", default=DEFAULT_DB_PATH, help="SQLite store path")
    parser.add_argument(
        "--no-graph",
        action="store_true",
        help="Skip replay text and the Mermaid DAG (plain narrative only)",
    )
    parser.add_argument(
        "--format",
        choices=("text", "json"),
        default="text",
        help="Output format: text narrative (default) or machine-readable JSON (C20)",
    )
    return parser


def run_demo(argv: list[str]) -> int:
    """Run the demo subcommand; return a process exit code.

    Args:
        argv: Arguments for the ``demo`` subcommand.

    Returns:
        0 on a completed narrative, 1 on a goal-validation failure.
    """
    args = build_demo_parser().parse_args(argv)
    store = _open_store(args.store)
    try:
        return run_demo_engine(
            args.goal,
            store,
            no_graph=args.no_graph,
            output_format=args.format,
        )
    finally:
        store.close()


def _open_store(path: str) -> SQLiteStore | InMemoryStore:
    """Open the SQLite store, falling back to memory when it is down."""
    try:
        return SQLiteStore(path)
    except StoreUnavailable as err:
        print(f"warning: {err}; continuing in memory — data will not be persisted")
        return InMemoryStore()


__all__ = ["build_demo_parser", "run_demo"]
