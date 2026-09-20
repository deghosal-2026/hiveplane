"""Pluggable plan store package (F-09): protocol, in-memory, and SQLite.

The store persists every plan revision, its critique findings, escalations,
and execution traces so the full planning history is diff-able and
replay-able (§2.1). See :mod:`planner_critic.store.base` for the protocol and
the side-channel contract.
"""

from __future__ import annotations

from .base import InMemoryStore, PlanDiff, PlanStore, StoreUnavailable
from .sqlite import SQLiteStore
from .versions import MIGRATIONS, SCHEMA_VERSION, apply_migrations, revert_migrations

__all__ = [
    "MIGRATIONS",
    "SCHEMA_VERSION",
    "InMemoryStore",
    "PlanDiff",
    "PlanStore",
    "SQLiteStore",
    "StoreUnavailable",
    "apply_migrations",
    "revert_migrations",
]
