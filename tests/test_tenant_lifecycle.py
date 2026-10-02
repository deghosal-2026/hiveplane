"""Tenant lifecycle: status substrate (M58-06)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, inspect, text

from hiveplane.tenancy import TenantStatus
from hiveplane.tenancy.context import SYSTEM_CONTEXT, TenantContext
from hiveplane.tenancy.errors import TenantNotFoundError, TenantScopeError
from hiveplane.tenancy.models import Role, Tenant
from hiveplane.tenancy.store import InMemoryTenantStore, PostgresTenantStore
from postgres import ensure_schema

_NOW = datetime(2026, 9, 25, tzinfo=UTC)
_ACME = TenantContext(tenant_id="acme", role=Role.ADMIN)


def _tenant(tenant_id: str = "acme") -> Tenant:
    return Tenant(tenant_id=tenant_id, name=tenant_id.title(), created_at=_NOW)


def test_tenant_status_defaults_active() -> None:
    assert _tenant().status == TenantStatus.ACTIVE


def test_set_status_round_trips_in_memory() -> None:
    store = InMemoryTenantStore()
    store.save_tenant(SYSTEM_CONTEXT, _tenant())

    updated = store.set_status(_ACME, "acme", TenantStatus.SUSPENDED)

    assert updated.status == TenantStatus.SUSPENDED
    assert store.get_tenant(_ACME, "acme") == updated


def test_set_status_unknown_tenant_raises() -> None:
    store = InMemoryTenantStore()
    with pytest.raises(TenantNotFoundError):
        store.set_status(SYSTEM_CONTEXT, "ghost", TenantStatus.SUSPENDED)


def test_set_status_rejects_foreign_context() -> None:
    store = InMemoryTenantStore()
    store.save_tenant(SYSTEM_CONTEXT, _tenant())
    with pytest.raises(TenantScopeError):
        store.set_status(TenantContext(tenant_id="other"), "acme", TenantStatus.SUSPENDED)


def test_postgres_set_status_persists_column(pg_engine: Engine) -> None:
    ensure_schema(pg_engine)
    if "status" not in {column["name"] for column in inspect(pg_engine).get_columns("tenants")}:
        pytest.skip("tenants.status not migrated yet")
    store = PostgresTenantStore(pg_engine)
    store.clear()
    store.save_tenant(SYSTEM_CONTEXT, _tenant())

    store.set_status(_ACME, "acme", TenantStatus.SUSPENDED)

    stored = store.get_tenant(_ACME, "acme")
    assert stored is not None
    assert stored.status == TenantStatus.SUSPENDED
    with pg_engine.connect() as connection:
        status: str = connection.execute(
            text("SELECT status FROM tenants WHERE tenant_id = 'acme'")
        ).scalar_one()
    assert status == "suspended"


def test_postgres_set_status_unknown_tenant_raises(pg_engine: Engine) -> None:
    ensure_schema(pg_engine)
    store = PostgresTenantStore(pg_engine)
    store.clear()
    with pytest.raises(TenantNotFoundError):
        store.set_status(SYSTEM_CONTEXT, "ghost", TenantStatus.SUSPENDED)


def test_postgres_save_tenant_updates_status(pg_engine: Engine) -> None:
    ensure_schema(pg_engine)
    store = PostgresTenantStore(pg_engine)
    store.clear()
    store.save_tenant(SYSTEM_CONTEXT, _tenant())
    suspended = _tenant().model_copy(update={"status": TenantStatus.SUSPENDED})

    store.save_tenant(SYSTEM_CONTEXT, suspended)

    stored = store.get_tenant(_ACME, "acme")
    assert stored is not None
    assert stored.status == TenantStatus.SUSPENDED

