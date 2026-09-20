"""``plancritic migrate`` — reversible store-schema management (F-27).

Drives the migration registry in :mod:`planner_critic.store.versions` against
a SQLite store file: apply pending migrations, revert applied ones, and report
the current schema version. Old schema versions stay readable because
migrations add/replace rather than destroy, and every plan row keeps its
``plan_schema_version``.
"""

from __future__ import annotations

import argparse
import sqlite3

from ..store.base import StoreUnavailable
from ..store.versions import (
    SCHEMA_VERSION,
    apply_migrations,
    revert_migrations,
)

DEFAULT_DB_PATH = ".plancritic/plans.db"


def build_migrate_parser() -> argparse.ArgumentParser:
    """Build the ``migrate`` subcommand parser."""
    parser = argparse.ArgumentParser(
        prog="plancritic migrate",
        description="Manage the plan-store schema (F-27)",
        add_help=False,
    )
    parser.add_argument(
        "--path",
        default=DEFAULT_DB_PATH,
        help="SQLite store path (default: %(default)s)",
    )
    parser.add_argument(
        "--to",
        type=int,
        default=None,
        help="Target schema version (default: latest)",
    )
    parser.add_argument(
        "--revert",
        action="store_true",
        help="Revert to --to (default 0, dropping the schema)",
    )
    return parser


def run_migrate(argv: list[str]) -> int:
    """Apply or revert store migrations; return a process exit code.

    Args:
        argv: Arguments for the ``migrate`` subcommand.

    Returns:
        0 on success, 1 on a store/migration failure.
    """
    args = build_migrate_parser().parse_args(argv)
    try:
        conn = sqlite3.connect(args.path)
        try:
            if args.revert:
                target = 0 if args.to is None else args.to
                reached = revert_migrations(conn, target)
                print(f"reverted to schema v{reached}")
            else:
                reached = apply_migrations(conn, args.to)
                print(f"schema at v{reached} (latest v{SCHEMA_VERSION})")
        finally:
            conn.close()
    except (sqlite3.Error, StoreUnavailable) as err:
        print(f"migrate failed: {err}")
        return 1
    return 0
