"""``plancritic replay`` — walk plan version history (F-76).

Replays every stored revision of a plan, optionally limited by ``--step``
and rendered as JSON via ``--format json``.
"""

from __future__ import annotations

import argparse

from ..store.base import StoreUnavailable
from ..store.sqlite import SQLiteStore
from ..viz.replay import replay

DEFAULT_DB_PATH = ".plancritic/plans.db"


def build_replay_parser() -> argparse.ArgumentParser:
    """Build the ``replay`` subcommand parser."""
    parser = argparse.ArgumentParser(
        prog="plancritic replay",
        description="Walk plan version history (F-76)",
        add_help=False,
    )
    parser.add_argument("--store", default=DEFAULT_DB_PATH, help="SQLite store path")
    parser.add_argument("plan_id", help="Plan id to replay")
    parser.add_argument("--step", type=int, default=None, help="Max revisions to show")
    parser.add_argument("--format", choices=["text", "json"], default="text", help="Output format")
    return parser


def run_replay(argv: list[str]) -> int:
    """Run the replay subcommand; return a process exit code.

    Args:
        argv: Arguments for the ``replay`` subcommand.

    Returns:
        0 on success, 1 on a store failure.
    """
    args = build_replay_parser().parse_args(argv)
    try:
        store = SQLiteStore(args.store)
        try:
            result = replay(store, args.plan_id, step=args.step, fmt=args.format)
        finally:
            store.close()
    except StoreUnavailable as err:
        print(f"replay failed: {err}")
        return 1

    if not result.steps:
        print(f"no history for plan {args.plan_id!r}")
        return 0

    if args.format == "json":
        print(result.to_json())
    else:
        for step in result.steps:
            print(f"v{step.version}: {len(step.plan.tasks)} tasks, {len(step.findings)} findings")
            for f in step.findings:
                print(f"    [{f.severity.value}] {f.reason_code}: {f.message}")
    return 0


__all__ = ["build_replay_parser", "run_replay"]
