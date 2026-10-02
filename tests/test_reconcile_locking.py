"""Reconcile concurrency-safety tests (M26-07)."""

from __future__ import annotations

from sqlalchemy import Engine

from hiveplane.reconcile.locking import (
    InMemoryReconcileLock,
    PostgresReconcileLock,
)


def test_in_memory_lock_is_exclusive_per_source() -> None:
    lock = InMemoryReconcileLock()
    with lock.hold("git-main") as first:
        assert first is True
        with lock.hold("git-main") as second:
            assert second is False
        with lock.hold("git-other") as other:
            assert other is True
    with lock.hold("git-main") as again:
        assert again is True


def test_postgres_lock_is_exclusive_across_instances(pg_engine: Engine) -> None:
    first = PostgresReconcileLock(pg_engine)
    second = PostgresReconcileLock(pg_engine)
    with first.hold("git-main") as acquired:
        assert acquired is True
        with second.hold("git-main") as contended:
            assert contended is False
        with second.hold("git-other") as other:
            assert other is True
    with second.hold("git-main") as released:
        assert released is True
