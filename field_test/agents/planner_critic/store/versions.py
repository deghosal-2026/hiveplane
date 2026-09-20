"""Reversible store-schema migrations (F-27, PRD 08).

Evolving the plan schema can silently break stored plans; the mitigation is
versioned, tested, **reversible** migrations with ``plan_schema_version`` on
every row and old schema versions kept readable (PRD 08).

The registry maps each schema version to an idempotent ``up``/``down`` pair
that operates on a raw :class:`sqlite3.Connection`. The SQLite store applies
pending migrations on open; ``plancritic migrate`` drives the same registry
from the CLI (up/down/to/current).
"""

from __future__ import annotations

import sqlite3
from dataclasses import dataclass

from .base import StoreUnavailable

INITIAL_SCHEMA_VERSION = 1

_INITIAL_SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS plan_versions (
    plan_id             TEXT NOT NULL,
    goal_id             TEXT NOT NULL,
    plan_schema_version TEXT NOT NULL,
    version             INTEGER NOT NULL,
    parent_version      TEXT,
    created_at          TEXT NOT NULL,
    body                TEXT NOT NULL,
    PRIMARY KEY (plan_id, version)
);
CREATE TABLE IF NOT EXISTS findings (
    plan_id TEXT NOT NULL,
    version INTEGER NOT NULL,
    body    TEXT NOT NULL,
    PRIMARY KEY (plan_id, version)
);
CREATE TABLE IF NOT EXISTS escalations (
    plan_id TEXT NOT NULL,
    body    TEXT NOT NULL,
    PRIMARY KEY (plan_id)
);
CREATE TABLE IF NOT EXISTS execution_traces (
    plan_id TEXT NOT NULL,
    seq     INTEGER PRIMARY KEY AUTOINCREMENT,
    body    TEXT NOT NULL
);
CREATE TABLE IF NOT EXISTS links (
    plan_id  TEXT NOT NULL,
    version  INTEGER NOT NULL,
    trace_id TEXT NOT NULL,
    PRIMARY KEY (plan_id, version, trace_id)
);
CREATE INDEX IF NOT EXISTS idx_plan_goal
    ON plan_versions (goal_id, plan_id, version DESC);
CREATE INDEX IF NOT EXISTS idx_trace_plan
    ON execution_traces (plan_id);
"""


@dataclass(frozen=True)
class Migration:
    """A reversible schema migration.

    Attributes:
        version: Monotonic schema version this migration produces.
        name: Short human-readable label.
        up: DDL that moves the schema *to* this version.
        down: DDL that reverses ``up`` (moves back to the previous version).
    """

    version: int
    name: str
    up: str
    down: str


MIGRATIONS: tuple[Migration, ...] = (
    Migration(
        version=INITIAL_SCHEMA_VERSION,
        name="initial store schema",
        up=_INITIAL_SCHEMA_SQL,
        down="""
        DROP TABLE IF EXISTS links;
        DROP TABLE IF EXISTS execution_traces;
        DROP TABLE IF EXISTS escalations;
        DROP TABLE IF EXISTS findings;
        DROP TABLE IF EXISTS plan_versions;
        """,
    ),
    Migration(
        version=2,
        name="replan links table",
        up="""
        CREATE TABLE IF NOT EXISTS replan_links (
            plan_id          TEXT NOT NULL,
            version          INTEGER NOT NULL,
            parent_plan_id   TEXT NOT NULL,
            parent_version   INTEGER NOT NULL,
            policy           TEXT NOT NULL,
            partial_execution TEXT,
            body             TEXT NOT NULL,
            PRIMARY KEY (plan_id, version)
        );
        CREATE INDEX IF NOT EXISTS idx_replan_parent
            ON replan_links (parent_plan_id, parent_version);
        """,
        down="""
        DROP TABLE IF EXISTS replan_links;
        """,
    ),
    Migration(
        version=3,
        name="missed critiques",
        up="""
        CREATE TABLE IF NOT EXISTS missed_critiques (
            plan_id TEXT NOT NULL PRIMARY KEY,
            body    TEXT NOT NULL
        );
        """,
        down="""
        DROP TABLE IF EXISTS missed_critiques;
        """,
    ),
    Migration(
        version=4,
        name="plan signatures",
        up="""
        CREATE TABLE IF NOT EXISTS plan_signatures (
            plan_id   TEXT NOT NULL,
            version   INTEGER NOT NULL,
            signature TEXT NOT NULL,
            PRIMARY KEY (plan_id, version)
        );
        CREATE INDEX IF NOT EXISTS idx_sig_plan
            ON plan_signatures (plan_id, version);
        """,
        down="""
        DROP TABLE IF EXISTS plan_signatures;
        """,
    ),
)

#: Highest schema version known to this release.
SCHEMA_VERSION = MIGRATIONS[-1].version


def ensure_migration_table(conn: sqlite3.Connection) -> None:
    """Create the ``schema_migrations`` ledger if it does not exist.

    Args:
        conn: Open connection to migrate.
    """
    conn.execute(
        """
        CREATE TABLE IF NOT EXISTS schema_migrations (
            version    INTEGER NOT NULL,
            name       TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
            PRIMARY KEY (version)
        )
        """
    )


def current_schema_version(conn: sqlite3.Connection) -> int:
    """Return the highest applied migration version.

    Args:
        conn: Open connection.

    Returns:
        The applied schema version (0 when none have run).
    """
    ensure_migration_table(conn)
    row = conn.execute("SELECT COALESCE(MAX(version), 0) AS v FROM schema_migrations").fetchone()
    return int(row[0]) if row else 0


def apply_migrations(conn: sqlite3.Connection, target: int | None = None) -> int:
    """Apply pending migrations up to ``target`` (default: latest).

    Args:
        conn: Open connection.
        target: Schema version to reach; None means latest.

    Returns:
        The schema version after applying.

    Raises:
        StoreUnavailable: if a migration fails to apply (rolled back).
    """
    ensure_migration_table(conn)
    desired = SCHEMA_VERSION if target is None else target
    current = current_schema_version(conn)
    if desired < current:
        raise StoreUnavailable(f"cannot migrate up to {desired}: already at {current}")
    for migration in MIGRATIONS:
        if migration.version <= current:
            continue
        if migration.version > desired:
            break
        try:
            with conn:
                conn.executescript(migration.up)
                conn.execute(
                    "INSERT INTO schema_migrations (version, name) VALUES (?, ?)",
                    (migration.version, migration.name),
                )
        except sqlite3.Error as err:
            raise StoreUnavailable(
                f"migration v{migration.version} ({migration.name}) failed: {err}"
            ) from err
        current = migration.version
    return current


def revert_migrations(conn: sqlite3.Connection, target: int = 0) -> int:
    """Reverse applied migrations down to ``target``.

    Args:
        conn: Open connection.
        target: Schema version to revert to (0 drops the schema).

    Returns:
        The schema version after reverting.

    Raises:
        StoreUnavailable: if a migration fails to reverse.
    """
    ensure_migration_table(conn)
    current = current_schema_version(conn)
    if target > current:
        raise StoreUnavailable(f"cannot revert to {target}: currently at {current}")
    for migration in reversed(MIGRATIONS):
        if migration.version > current:
            continue
        if migration.version <= target:
            break
        try:
            with conn:
                conn.executescript(migration.down)
                conn.execute(
                    "DELETE FROM schema_migrations WHERE version = ?",
                    (migration.version,),
                )
        except sqlite3.Error as err:
            raise StoreUnavailable(
                f"revert v{migration.version} ({migration.name}) failed: {err}"
            ) from err
        current = migration.version - 1
    return current
