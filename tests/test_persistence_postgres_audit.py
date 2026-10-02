"""PostgresAuditLog persistence and tamper detection (Postgres-gated)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import Engine, text

from hiveplane.persistence.postgres_audit import PostgresAuditLog
from postgres import ensure_schema


def _clocked(*times: datetime) -> Callable[[], datetime]:
    remaining = list(times)
    return lambda: remaining.pop(0)


def test_audit_persists_and_verifies(pg_engine: Engine) -> None:
    log = PostgresAuditLog(pg_engine)
    ensure_schema(pg_engine)
    log.clear()
    log.append("operator", "pause", "run-1")
    log.append("operator", "stop", "run-1")

    reopened = PostgresAuditLog(pg_engine)
    assert [record.action for record in reopened.records()] == ["pause", "stop"]
    assert reopened.verify() is True


def test_tampering_is_detected(pg_engine: Engine) -> None:
    log = PostgresAuditLog(pg_engine)
    ensure_schema(pg_engine)
    log.clear()
    log.append("operator", "pause", "run-1")
    with pg_engine.begin() as connection:
        connection.execute(text("UPDATE audit_log SET actor = 'intruder'"))

    reopened = PostgresAuditLog(pg_engine)
    assert reopened.verify() is False


def test_prune_persists_anchor_across_reopen(pg_engine: Engine) -> None:
    ensure_schema(pg_engine)
    log = PostgresAuditLog(
        pg_engine,
        clock=_clocked(
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
            datetime(2026, 1, 3, tzinfo=UTC),
        ),
    )
    log.clear()
    log.append("operator", "a", "run-1")
    second = log.append("operator", "b", "run-1")
    log.append("operator", "c", "run-1")

    pruned = log.prune(before=datetime(2026, 1, 2, 12, tzinfo=UTC))
    assert pruned == 2

    reopened = PostgresAuditLog(pg_engine)
    assert [record.action for record in reopened.records()] == ["c"]
    assert reopened.anchor() == second.hash
    assert reopened.verify() is True

    reopened.append("operator", "d", "run-1")
    assert reopened.verify() is True


def test_prune_stops_at_protected_record(pg_engine: Engine) -> None:
    ensure_schema(pg_engine)
    log = PostgresAuditLog(
        pg_engine,
        clock=_clocked(
            datetime(2026, 1, 1, tzinfo=UTC),
            datetime(2026, 1, 2, tzinfo=UTC),
            datetime(2026, 1, 3, tzinfo=UTC),
            datetime(2026, 1, 4, tzinfo=UTC),
        ),
    )
    log.clear()
    for action in ("a", "b", "c", "d"):
        log.append("operator", action, "run-1")

    pruned = log.prune(
        before=datetime(2026, 1, 5, tzinfo=UTC),
        protect=lambda record: record.action == "b",
    )

    assert pruned == 1
    assert [record.action for record in log.records()] == ["b", "c", "d"]
    assert log.verify() is True

