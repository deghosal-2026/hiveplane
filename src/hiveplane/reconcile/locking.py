"""Single-writer coordination for reconcile passes (M26-07, D22).

Reconcile is single-writer per source. An in-memory lock covers a single
process; the Postgres implementation takes a session-level advisory lock keyed by
source id, so across replicas exactly one controller acts and the rest stay
passive. The lock is released on exit or connection loss.
"""

from __future__ import annotations

import hashlib
import threading
from collections.abc import Iterator
from contextlib import AbstractContextManager, contextmanager
from typing import Protocol

from sqlalchemy import Engine, func, select


class ReconcileLock(Protocol):
    """Exclusive per-source lock guarding a reconcile pass."""

    def hold(self, source_id: str) -> AbstractContextManager[bool]: ...


class InMemoryReconcileLock:
    """A process-local, thread-safe per-source lock."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._held: set[str] = set()

    @contextmanager
    def hold(self, source_id: str) -> Iterator[bool]:
        """Yield True when this caller acquired the lock, False when contended."""
        with self._lock:
            acquired = source_id not in self._held
            if acquired:
                self._held.add(source_id)
        try:
            yield acquired
        finally:
            if acquired:
                with self._lock:
                    self._held.discard(source_id)


class PostgresReconcileLock:
    """A cross-replica advisory lock backed by PostgreSQL."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine

    @contextmanager
    def hold(self, source_id: str) -> Iterator[bool]:
        """Yield True when the advisory lock was taken, False when held elsewhere."""
        key = advisory_key(source_id)
        connection = self._engine.connect()
        try:
            acquired = bool(
                connection.execute(select(func.pg_try_advisory_lock(key))).scalar_one()
            )
            try:
                yield acquired
            finally:
                if acquired:
                    connection.execute(select(func.pg_advisory_unlock(key)))
        finally:
            connection.close()


def advisory_key(source_id: str) -> int:
    """Return a stable signed 64-bit advisory-lock key for a source id."""
    digest = hashlib.blake2b(source_id.encode("utf-8"), digest_size=8).digest()
    return int.from_bytes(digest, "big", signed=True)
