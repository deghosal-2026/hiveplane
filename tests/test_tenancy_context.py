import pytest

from hiveplane.tenancy import Role, TenantScopeError
from hiveplane.tenancy.context import (
    DEFAULT_ATTRIBUTION_KEY,
    DEFAULT_CONTEXT,
    DEFAULT_TENANT_ID,
    SYSTEM_CONTEXT,
    TenantContext,
)


def test_context_scopes_only_its_tenant() -> None:
    ctx = TenantContext(tenant_id="acme", role=Role.ADMIN)
    assert ctx.scopes("acme")
    assert not ctx.scopes("other")


def test_require_raises_on_foreign_tenant() -> None:
    ctx = TenantContext(tenant_id="acme")
    with pytest.raises(TenantScopeError):
        ctx.require("other")
    ctx.require("acme")


def test_system_context_bypasses_scoping() -> None:
    assert SYSTEM_CONTEXT.is_system
    assert SYSTEM_CONTEXT.scopes("anything")
    SYSTEM_CONTEXT.require("anything")


def test_default_context_is_default_tenant_and_team() -> None:
    assert DEFAULT_CONTEXT.tenant_id == DEFAULT_TENANT_ID
    assert DEFAULT_CONTEXT.team_id == "default"
    assert DEFAULT_CONTEXT.attribution_key == DEFAULT_ATTRIBUTION_KEY


def test_context_is_frozen() -> None:
    ctx = TenantContext(tenant_id="acme")
    with pytest.raises(Exception):  # noqa: B017
        ctx.tenant_id = "other"  # type: ignore[misc]
