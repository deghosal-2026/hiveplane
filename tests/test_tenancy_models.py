from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from hiveplane.tenancy import Membership, Role, Team, Tenant
from hiveplane.tenancy.errors import TenantScopeError

_NOW = datetime(2026, 9, 25, tzinfo=UTC)


def test_tenant_accepts_valid_fields() -> None:
    tenant = Tenant(tenant_id="acme", name="Acme", created_at=_NOW)
    assert tenant.tenant_id == "acme"


def test_tenant_rejects_empty_id() -> None:
    with pytest.raises(ValidationError):
        Tenant(tenant_id="", name="Acme", created_at=_NOW)


def test_tenant_forbids_extra_fields() -> None:
    with pytest.raises(ValidationError):
        Tenant(tenant_id="acme", name="Acme", created_at=_NOW, nope=1)  # type: ignore[call-arg]


def test_team_is_tenant_qualified() -> None:
    team = Team(
        team_id="platform",
        tenant_id="acme",
        name="Platform",
        attribution_key="acme.platform",
        created_at=_NOW,
    )
    assert team.attribution_key == "acme.platform"


def test_membership_role_is_enum() -> None:
    membership = Membership(
        membership_id="m1",
        tenant_id="acme",
        team_id="platform",
        operator_id="alice",
        role="admin",  # type: ignore[arg-type]
        created_at=_NOW,
    )
    assert membership.role is Role.ADMIN


def test_scope_error_carries_tenant_and_detail() -> None:
    error = TenantScopeError("acme", "cannot access tenant 'other'")
    assert error.tenant_id == "acme"
    assert "other" in str(error)
