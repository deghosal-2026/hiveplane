"""Shared helpers for Postgres-gated tests (skip when no database is reachable)."""

from __future__ import annotations

import pytest
from sqlalchemy import Engine, create_engine, text
from sqlalchemy.exc import SQLAlchemyError

from hiveplane.config import get_settings


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
