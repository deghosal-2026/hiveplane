"""Tests for persistence wiring."""

from __future__ import annotations

from pathlib import Path

from sqlalchemy import Engine

from hiveplane.execution.store import InMemoryRunStore
from hiveplane.execution.wiring import build_run_store

_ROOT = Path(__file__).resolve().parents[1]


def test_memory_store_selection() -> None:
    assert isinstance(build_run_store(store="memory"), InMemoryRunStore)


def test_postgres_store_selection(pg_engine: Engine) -> None:
    from hiveplane.persistence.run_store import PostgresRunStore

    store = build_run_store(store="postgres")
    assert isinstance(store, PostgresRunStore)
    store.clear()


def test_ci_has_a_postgres_service() -> None:
    workflow = (_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "postgres:16" in workflow
    assert "alembic upgrade head" in workflow
