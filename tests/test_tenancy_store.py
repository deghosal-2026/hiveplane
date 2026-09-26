"""Tests for the tenant/team/membership store (#149)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hiveplane.tenancy import Membership, Role, Team, Tenant, TenantScopeError
from hiveplane.tenancy.context import SYSTEM_CONTEXT, TenantContext
from hiveplane.tenancy.errors import TenantNotFoundError
from hiveplane.tenancy.store import InMemoryTenantStore

_NOW = datetime(2026, 9, 25, tzinfo=UTC)
_ACME = TenantContext(tenant_id="acme", role=Role.ADMIN)


def _tenant(tenant_id: str) -> Tenant:
    return Tenant(tenant_id=tenant_id, name=tenant_id.title(), created_at=_NOW)


def test_tenant_store_round_trips() -> None:
    store = InMemoryTenantStore()
    store.save_tenant(SYSTEM_CONTEXT, _tenant("acme"))
    assert store.get_tenant(_ACME, "acme") == _tenant("acme")


def test_tenant_store_hides_foreign_tenants() -> None:
    store = InMemoryTenantStore()
    store.save_tenant(SYSTEM_CONTEXT, _tenant("acme"))
    assert store.get_tenant(TenantContext(tenant_id="other"), "acme") is None


def test_team_save_requires_matching_tenant() -> None:
    store = InMemoryTenantStore()
    store.save_tenant(SYSTEM_CONTEXT, _tenant("acme"))
    team = Team(
        team_id="platform",
        tenant_id="acme",
        name="Platform",
        attribution_key="acme.platform",
        created_at=_NOW,
    )
    store.save_team(_ACME, team)
    assert store.get_team(_ACME, "acme", "platform") == team
    with pytest.raises(TenantScopeError):
        store.save_team(TenantContext(tenant_id="other"), team)


def test_team_requires_existing_tenant() -> None:
    store = InMemoryTenantStore()
    team = Team(
        team_id="platform",
        tenant_id="acme",
        name="Platform",
        attribution_key="acme.platform",
        created_at=_NOW,
    )
    with pytest.raises(TenantNotFoundError):
        store.save_team(_ACME, team)


def test_membership_listing_is_tenant_scoped() -> None:
    store = InMemoryTenantStore()
    store.save_tenant(SYSTEM_CONTEXT, _tenant("acme"))
    membership = Membership(
        membership_id="m1",
        tenant_id="acme",
        team_id=None,
        operator_id="alice",
        role=Role.ADMIN,
        created_at=_NOW,
    )
    store.save_membership(_ACME, membership)
    assert store.list_memberships(_ACME, "acme") == [membership]
    assert store.list_memberships(TenantContext(tenant_id="other"), "acme") == []
