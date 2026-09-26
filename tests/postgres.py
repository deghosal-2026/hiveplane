"""Shared helpers for Postgres-gated tests (skip when no database is reachable)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from hiveplane.config import get_settings
from hiveplane.persistence import models  # noqa: F401  (registers tables)
from hiveplane.persistence.base import Base, session_factory
from hiveplane.persistence.models import WorkloadRow

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def postgres_engine() -> Engine:
    """Return an engine for the configured database, or skip the test."""
    get_settings.cache_clear()
    url = get_settings().database.url
    try:
        engine = create_engine(url, pool_pre_ping=True)
        with engine.connect() as connection:
            connection.execute(text("SELECT 1"))
    except SQLAlchemyError as exc:
        pytest.skip(f"postgres not available at {url}: {exc}")
    return engine


def ensure_schema(engine: Engine) -> None:
    """Create the current schema if it is absent (idempotent)."""
    Base.metadata.create_all(engine)


def reset_database(engine: Engine) -> None:
    """Wipe every table in one FK-safe statement, for per-test isolation."""
    ensure_schema(engine)
    tables = ", ".join(Base.metadata.tables)
    with engine.begin() as connection:
        connection.execute(text(f"TRUNCATE {tables} CASCADE"))


def seed_workload(
    engine: Engine, name: str = "agent-1", tenant_id: str = "default"
) -> None:
    """Ensure a workload row exists so run/workload-referencing rows satisfy FKs."""
    ensure_schema(engine)
    with session_factory(engine).begin() as session:
        if session.get(WorkloadRow, name) is None:
            session.add(
                WorkloadRow(
                    name=name,
                    owner="operator",
                    team="platform",
                    certification_status="certified",
                    current_version=1,
                    created_at=_NOW,
                    updated_at=_NOW,
                    tenant_id=tenant_id,
                    payload={},
                )
            )
