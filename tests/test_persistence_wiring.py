"""Tests for persistence wiring (#118)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest
from alembic.config import Config
from sqlalchemy import Engine

from hiveplane.api.app import create_app
from hiveplane.budget.store import InMemoryBudgetStore, PostgresBudgetStore, build_budget_store
from hiveplane.certification.store import (
    InMemoryCertificationStore,
    PostgresCertificationStore,
    build_certification_store,
)
from hiveplane.config import get_settings
from hiveplane.core.approval import ApprovalRecord
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.store import InMemoryRunStore
from hiveplane.execution.wiring import build_run_store
from hiveplane.persistence.base import create_engine_from_settings
from hiveplane.policy.store import (
    InMemoryApprovalStore,
    PostgresApprovalStore,
    build_approval_store,
)
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import (
    InMemoryRegistryStore,
    PostgresRegistryStore,
    build_registry_store,
)
from postgres import ensure_schema, reset_database

_ROOT = Path(__file__).resolve().parents[1]
_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_memory_store_selection() -> None:
    assert isinstance(build_run_store(store="memory"), InMemoryRunStore)


def test_postgres_store_selection(pg_engine: Engine) -> None:
    from hiveplane.persistence.run_store import PostgresRunStore

    store = build_run_store(store="postgres")
    ensure_schema(pg_engine)
    assert isinstance(store, PostgresRunStore)
    store.clear()


def test_ci_has_a_postgres_service() -> None:
    workflow = (_ROOT / ".github/workflows/ci.yml").read_text(encoding="utf-8")
    assert "postgres:16" in workflow
    assert "alembic upgrade head" in workflow


def test_builders_default_to_in_memory() -> None:
    settings = get_settings()
    assert isinstance(build_registry_store(settings), InMemoryRegistryStore)
    assert isinstance(build_approval_store(settings), InMemoryApprovalStore)
    assert isinstance(build_budget_store(settings), InMemoryBudgetStore)
    assert isinstance(build_certification_store(settings), InMemoryCertificationStore)


def test_builders_select_postgres() -> None:
    settings = get_settings().model_copy(
        update={"execution": get_settings().execution.model_copy(update={"store": "postgres"})}
    )
    assert isinstance(build_registry_store(settings), PostgresRegistryStore)
    assert isinstance(build_approval_store(settings), PostgresApprovalStore)
    assert isinstance(build_budget_store(settings), PostgresBudgetStore)
    assert isinstance(build_certification_store(settings), PostgresCertificationStore)


def test_create_app_defaults_to_in_memory() -> None:
    app = create_app()

    assert isinstance(app.state.budget_store, InMemoryBudgetStore)
    assert isinstance(app.state.certification_coordinator.store, InMemoryCertificationStore)
    assert isinstance(app.state.registry_service._store, InMemoryRegistryStore)
    assert isinstance(app.state.approval_service._store, InMemoryApprovalStore)


def test_create_app_uses_postgres_stores_when_configured(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HIVEPLANE_EXECUTION__STORE", "postgres")
    get_settings.cache_clear()

    app = create_app()

    assert isinstance(app.state.budget_store, PostgresBudgetStore)
    assert isinstance(app.state.certification_coordinator.store, PostgresCertificationStore)
    assert isinstance(app.state.registry_service._store, PostgresRegistryStore)
    assert isinstance(app.state.approval_service._store, PostgresApprovalStore)


def test_create_app_adapter_executor_is_selected(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HIVEPLANE_CERTIFICATION__EXECUTOR", "adapter")
    get_settings.cache_clear()

    app = create_app()

    assert app.state.certification_coordinator is not None


def test_run_migrations_upgrades_head(monkeypatch: pytest.MonkeyPatch) -> None:
    configs: list[Config] = []
    revisions: list[str] = []

    def _fake_upgrade(config: Config, revision: str, **kwargs: object) -> None:
        configs.append(config)
        revisions.append(revision)

    monkeypatch.setattr("hiveplane.persistence.migrate.command.upgrade", _fake_upgrade)

    from hiveplane.persistence.migrate import run_migrations

    run_migrations()

    assert revisions == ["head"]
    script_location = configs[0].get_main_option("script_location") or ""
    assert Path(script_location).name == "migrations"


def test_postgres_registry_round_trip(
    pg_engine: Engine, make_manifest: Callable[..., AgentWorkload]
) -> None:
    store = PostgresRegistryStore(pg_engine)
    reset_database(pg_engine)
    RegistryService(store).create(make_manifest("agent-1"))

    fresh_engine = create_engine_from_settings()
    try:
        reopened = RegistryService(PostgresRegistryStore(fresh_engine))
        assert reopened.get("agent-1").current_version == 1
        assert [version.version for version in reopened.versions("agent-1")] == [1]
    finally:
        fresh_engine.dispose()


def test_postgres_approval_round_trip(pg_engine: Engine) -> None:
    store = PostgresApprovalStore(pg_engine)
    reset_database(pg_engine)
    store.save(
        ApprovalRecord(
            approval_id="appr-1",
            run_id="run-1",
            workload="agent-1",
            rule="needs-approval",
            reason="destructive tool",
            requested_at=_NOW,
        )
    )

    fresh_engine = create_engine_from_settings()
    try:
        reopened = PostgresApprovalStore(fresh_engine)
        fetched = reopened.get("appr-1")
        assert fetched is not None
        assert fetched.workload == "agent-1"
        assert [record.approval_id for record in reopened.list_approvals(run_id="run-1")] == [
            "appr-1"
        ]
    finally:
        fresh_engine.dispose()
