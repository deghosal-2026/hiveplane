"""PostgresAuditLog persistence and tamper detection (Postgres-gated)."""

from __future__ import annotations

from sqlalchemy import Engine, text

from hiveplane.persistence.postgres_audit import PostgresAuditLog
from postgres import ensure_schema


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
