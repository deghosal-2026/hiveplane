"""Tenant admin operations: create/suspend/reinstate/quota (M58-06)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine, inspect

from hiveplane.delivery import (
    DeliveryChannel,
    DeliveryDestination,
    DeliveryEnvelope,
    DeliveryEventType,
    DeliveryService,
    DeliveryStatus,
    InMemoryDeliveryStore,
)
from hiveplane.tenancy import Role, Tenant, TenantQuota, TenantStatus
from hiveplane.tenancy.admin import TenantAdminService
from hiveplane.tenancy.context import (
    DEFAULT_CONTEXT,
    SYSTEM_CONTEXT,
    TenantContext,
)
from hiveplane.tenancy.errors import (
    TenantAlreadyExistsError,
    TenantNotFoundError,
    TenantScopeError,
    TenantSuspendedError,
)
from hiveplane.tenancy.store import InMemoryTenantStore, PostgresTenantStore
from postgres import ensure_schema

_NOW = datetime(2026, 9, 25, tzinfo=UTC)
_VIEWER = TenantContext(tenant_id="acme", role=Role.VIEWER)


class _Sender:
    """A no-op delivery sender that records nothing."""

    def send(self, channel: object, target: str, payload: dict[str, object]) -> None:
        return None


def _admin() -> tuple[TenantAdminService, InMemoryTenantStore]:
    store = InMemoryTenantStore()
    return TenantAdminService(store, clock=lambda: _NOW), store


def _envelope(tenant_id: str) -> DeliveryEnvelope:
    return DeliveryEnvelope(event_type=DeliveryEventType.COMPLETED, tenant_id=tenant_id)


def test_tenant_quota_defaults_to_unbounded() -> None:
    quota = TenantQuota()
    assert quota.max_agents is None
    assert quota.max_monthly_usd is None
    assert quota.max_concurrent_runs is None


def test_tenant_defaults_to_empty_quota() -> None:
    tenant = Tenant(tenant_id="acme", name="Acme", created_at=_NOW)
    assert tenant.quota == TenantQuota()


def test_tenant_quota_forbids_extra_fields() -> None:
    from pydantic import ValidationError

    with pytest.raises(ValidationError):
        TenantQuota(nope=1)  # type: ignore[call-arg]


def test_create_tenant_persists_active_tenant() -> None:
    admin, store = _admin()

    tenant = admin.create_tenant(SYSTEM_CONTEXT, tenant_id="acme", name="Acme")

    assert tenant.status is TenantStatus.ACTIVE
    assert tenant.quota == TenantQuota()
    assert store.get_tenant(SYSTEM_CONTEXT, "acme") == tenant


def test_create_tenant_stores_a_given_quota() -> None:
    admin, store = _admin()
    quota = TenantQuota(max_agents=3, max_monthly_usd=100.0)

    tenant = admin.create_tenant(
        SYSTEM_CONTEXT, tenant_id="acme", name="Acme", quota=quota
    )

    assert tenant.quota == quota
    stored = store.get_tenant(SYSTEM_CONTEXT, "acme")
    assert stored is not None
    assert stored.quota == quota


def test_create_tenant_rejects_existing_id() -> None:
    admin, _ = _admin()
    admin.create_tenant(SYSTEM_CONTEXT, tenant_id="acme", name="Acme")

    with pytest.raises(TenantAlreadyExistsError):
        admin.create_tenant(SYSTEM_CONTEXT, tenant_id="acme", name="Again")


def test_create_tenant_requires_system_context() -> None:
    admin, _ = _admin()
    tenant_admin = TenantContext(tenant_id="acme", role=Role.ADMIN)

    with pytest.raises(TenantScopeError):
        admin.create_tenant(tenant_admin, tenant_id="acme", name="Acme")
    with pytest.raises(TenantScopeError):
        admin.create_tenant(_VIEWER, tenant_id="acme", name="Acme")
    with pytest.raises(TenantScopeError):
        admin.create_tenant(DEFAULT_CONTEXT, tenant_id="acme", name="Acme")


def test_trusted_local_create_tenant_allows_local_admin() -> None:
    store = InMemoryTenantStore()
    admin = TenantAdminService(store, clock=lambda: _NOW, trusted_local=True)

    tenant = admin.create_tenant(DEFAULT_CONTEXT, tenant_id="acme", name="Acme")

    assert tenant.tenant_id == "acme"
    assert store.get_tenant(SYSTEM_CONTEXT, "acme") == tenant


def test_trusted_local_can_administer_another_tenant() -> None:
    store = InMemoryTenantStore()
    admin = TenantAdminService(store, clock=lambda: _NOW, trusted_local=True)
    admin.create_tenant(DEFAULT_CONTEXT, tenant_id="acme", name="Acme")

    suspended = admin.suspend_tenant(DEFAULT_CONTEXT, "acme")

    assert suspended.status is TenantStatus.SUSPENDED


def test_suspend_and_reinstate_round_trip() -> None:
    admin, store = _admin()
    admin.create_tenant(SYSTEM_CONTEXT, tenant_id="acme", name="Acme")

    suspended = admin.suspend_tenant(SYSTEM_CONTEXT, "acme", actor="alice")
    assert suspended.status is TenantStatus.SUSPENDED
    stored = store.get_tenant(SYSTEM_CONTEXT, "acme")
    assert stored is not None
    assert stored.status is TenantStatus.SUSPENDED

    reinstated = admin.reinstate_tenant(SYSTEM_CONTEXT, "acme", actor="alice")
    assert reinstated.status is TenantStatus.ACTIVE


def test_tenant_admin_can_suspend_its_own_tenant() -> None:
    admin, _ = _admin()
    admin.create_tenant(SYSTEM_CONTEXT, tenant_id="acme", name="Acme")
    acme_admin = TenantContext(tenant_id="acme", role=Role.ADMIN)

    suspended = admin.suspend_tenant(acme_admin, "acme")

    assert suspended.status is TenantStatus.SUSPENDED


def test_tenant_admin_cannot_suspend_another_tenant() -> None:
    admin, _ = _admin()
    admin.create_tenant(SYSTEM_CONTEXT, tenant_id="acme", name="Acme")
    admin.create_tenant(SYSTEM_CONTEXT, tenant_id="globex", name="Globex")
    acme_admin = TenantContext(tenant_id="acme", role=Role.ADMIN)

    with pytest.raises(TenantScopeError):
        admin.suspend_tenant(acme_admin, "globex")


def test_suspend_missing_tenant_raises() -> None:
    admin, _ = _admin()

    with pytest.raises(TenantNotFoundError):
        admin.suspend_tenant(SYSTEM_CONTEXT, "ghost")


def test_suspend_requires_admin() -> None:
    admin, _ = _admin()
    admin.create_tenant(SYSTEM_CONTEXT, tenant_id="acme", name="Acme")

    with pytest.raises(TenantScopeError):
        admin.suspend_tenant(_VIEWER, "acme")


def test_set_quota_round_trips() -> None:
    admin, store = _admin()
    admin.create_tenant(SYSTEM_CONTEXT, tenant_id="acme", name="Acme")
    quota = TenantQuota(max_concurrent_runs=5)

    updated = admin.set_quota(SYSTEM_CONTEXT, "acme", quota)

    assert updated.quota == quota
    stored = store.get_tenant(SYSTEM_CONTEXT, "acme")
    assert stored is not None
    assert stored.quota == quota


def test_tenant_admin_can_quota_its_own_tenant() -> None:
    admin, _ = _admin()
    admin.create_tenant(SYSTEM_CONTEXT, tenant_id="acme", name="Acme")
    acme_admin = TenantContext(tenant_id="acme", role=Role.ADMIN)

    updated = admin.set_quota(acme_admin, "acme", TenantQuota(max_agents=4))

    assert updated.quota.max_agents == 4


def test_tenant_admin_cannot_quota_another_tenant() -> None:
    admin, _ = _admin()
    admin.create_tenant(SYSTEM_CONTEXT, tenant_id="acme", name="Acme")
    admin.create_tenant(SYSTEM_CONTEXT, tenant_id="globex", name="Globex")
    acme_admin = TenantContext(tenant_id="acme", role=Role.ADMIN)

    with pytest.raises(TenantScopeError):
        admin.set_quota(acme_admin, "globex", TenantQuota(max_agents=4))


def test_require_active_returns_active_tenant() -> None:
    admin, _ = _admin()
    admin.create_tenant(SYSTEM_CONTEXT, tenant_id="acme", name="Acme")

    tenant = admin.require_active(DEFAULT_CONTEXT, "acme")

    assert tenant.tenant_id == "acme"
    assert tenant.status is TenantStatus.ACTIVE


def test_require_active_blocks_suspended_tenant() -> None:
    admin, _ = _admin()
    admin.create_tenant(SYSTEM_CONTEXT, tenant_id="acme", name="Acme")
    admin.suspend_tenant(SYSTEM_CONTEXT, "acme")

    with pytest.raises(TenantSuspendedError):
        admin.require_active(DEFAULT_CONTEXT, "acme")


def test_require_active_raises_for_missing_tenant() -> None:
    admin, _ = _admin()

    with pytest.raises(TenantNotFoundError):
        admin.require_active(DEFAULT_CONTEXT, "ghost")


def test_require_active_bypasses_suspension_for_system() -> None:
    admin, _ = _admin()
    admin.create_tenant(SYSTEM_CONTEXT, tenant_id="acme", name="Acme")
    admin.suspend_tenant(SYSTEM_CONTEXT, "acme")

    tenant = admin.require_active(SYSTEM_CONTEXT, "acme")

    assert tenant.status is TenantStatus.SUSPENDED


def test_list_tenants_delegates_to_store_scope() -> None:
    admin, _ = _admin()
    admin.create_tenant(SYSTEM_CONTEXT, tenant_id="acme", name="Acme")
    admin.create_tenant(SYSTEM_CONTEXT, tenant_id="globex", name="Globex")

    assert [t.tenant_id for t in admin.list_tenants(SYSTEM_CONTEXT)] == ["acme", "globex"]
    acme = TenantContext(tenant_id="acme", role=Role.ADMIN)
    assert [t.tenant_id for t in admin.list_tenants(acme)] == ["acme"]


def test_delivery_is_refused_for_a_suspended_destination() -> None:
    store = InMemoryTenantStore()
    admin = TenantAdminService(store, clock=lambda: _NOW)
    admin.create_tenant(SYSTEM_CONTEXT, tenant_id="acme", name="Acme")
    admin.suspend_tenant(SYSTEM_CONTEXT, "acme")

    def _gate(tenant_id: str) -> None:
        admin.require_active(DEFAULT_CONTEXT, tenant_id)

    delivery = DeliveryService(
        InMemoryDeliveryStore(),
        _Sender(),
        require_active=_gate,
    )

    with pytest.raises(TenantSuspendedError):
        delivery.deliver(
            _envelope("acme"),
            [DeliveryDestination(channel=DeliveryChannel.SLACK, target="#ops")],
        )


def test_delivery_proceeds_for_an_active_destination() -> None:
    store = InMemoryTenantStore()
    admin = TenantAdminService(store, clock=lambda: _NOW)
    admin.create_tenant(SYSTEM_CONTEXT, tenant_id="acme", name="Acme")

    def _gate(tenant_id: str) -> None:
        admin.require_active(DEFAULT_CONTEXT, tenant_id)

    delivery = DeliveryService(
        InMemoryDeliveryStore(),
        _Sender(),
        require_active=_gate,
    )

    attempts = delivery.deliver(
        _envelope("acme"),
        [DeliveryDestination(channel=DeliveryChannel.SLACK, target="#ops")],
    )

    assert [attempt.status for attempt in attempts] == [DeliveryStatus.DELIVERED]


def test_in_memory_set_quota_returns_updated_tenant() -> None:
    store = InMemoryTenantStore()
    store.save_tenant(SYSTEM_CONTEXT, Tenant(tenant_id="acme", name="Acme", created_at=_NOW))

    updated = store.set_quota(
        SYSTEM_CONTEXT, "acme", TenantQuota(max_agents=2)
    )

    assert updated.quota.max_agents == 2


def test_in_memory_set_quota_unknown_tenant_raises() -> None:
    store = InMemoryTenantStore()
    with pytest.raises(TenantNotFoundError):
        store.set_quota(SYSTEM_CONTEXT, "ghost", TenantQuota())


def test_postgres_quota_round_trips_through_payload(pg_engine: Engine) -> None:
    ensure_schema(pg_engine)
    if not inspect(pg_engine).has_table("tenants"):
        pytest.skip("tenants table not migrated yet")
    store = PostgresTenantStore(pg_engine)
    store.clear()
    admin = TenantAdminService(store, clock=lambda: _NOW)
    admin.create_tenant(SYSTEM_CONTEXT, tenant_id="acme", name="Acme")

    admin.set_quota(SYSTEM_CONTEXT, "acme", TenantQuota(max_agents=7))

    stored = store.get_tenant(SYSTEM_CONTEXT, "acme")
    assert stored is not None
    assert stored.quota.max_agents == 7
