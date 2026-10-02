"""M58 acceptance: multi-tenant isolation end-to-end (#457).

Proves the five M58 acceptance criteria through ``create_app()``:
(a) a tenant sees and acts only on its own resources;
(b) per-tenant budgets, policies, and keys are isolated and enforced;
(c) a suspended tenant cannot submit runs or authenticate;
(d) cross-tenant reads/writes return 403/404 consistently;
(e) tenant admin can create/suspend/reinstate and assign quotas.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
from hiveplane.api.deps import TENANT_HEADER
from hiveplane.core.run import AdmissionContext
from hiveplane.cost.models import BudgetScope, CostEvent
from hiveplane.fleet.cost import CostPeriodKind, CostType
from hiveplane.tenancy import Role
from hiveplane.tenancy.context import SYSTEM_CONTEXT, TenantContext
from hiveplane.tenancy.errors import TenantNotFoundError, TenantSuspendedError

ManifestFactory = Callable[..., Any]

_CTX_A = TenantContext(tenant_id="tenant-a", role=Role.ADMIN)
_CTX_B = TenantContext(tenant_id="tenant-b", role=Role.ADMIN)


def _headers(tenant: str) -> dict[str, str]:
    return {TENANT_HEADER: tenant}


def test_a_tenant_sees_and_acts_only_on_its_own_resources(
    make_manifest: ManifestFactory,
) -> None:
    app = create_app()
    app.state.registry_service.create(make_manifest(name="agent-a"), ctx=_CTX_A)
    client = TestClient(app)

    assert client.get("/workloads/agent-a", headers=_headers("tenant-a")).status_code == 200
    assert client.get("/workloads/agent-a", headers=_headers("tenant-b")).status_code == 404
    assert client.get("/workloads", headers=_headers("tenant-b")).json() == []
    assert (
        client.delete("/workloads/agent-a", headers=_headers("tenant-b")).status_code == 404
    )


def test_per_tenant_budgets_policies_and_keys_are_isolated(
    make_manifest: ManifestFactory,
) -> None:
    from test_tenant_scoping_isolation import _pack

    app = create_app()
    manifest = make_manifest(name="agent-a")

    app.state.cost_service.set_budget(
        "tenant-a",
        BudgetScope.TENANT,
        "tenant-a",
        CostPeriodKind.DAY,
        limit_usd=1.0,
        cap_usd=1.0,
        enforced=True,
        ctx=_CTX_A,
    )
    app.state.cost_service.record(
        CostEvent(
            event_id="ev-cap",
            tenant_id="tenant-a",
            team_id="platform",
            workload_id="agent-a",
            cost_type=CostType.LLM,
            cost_usd=5.0,
            occurred_at=datetime.now(UTC),
        ),
        ctx=_CTX_A,
    )
    app.state.policy_pack_registry.publish(_pack("tenant-a"), ctx=_CTX_A)
    key_a = app.state.auth_service.keys.create("tenant-a", Role.ADMIN, ctx=SYSTEM_CONTEXT)

    allowed_a = app.state.budget_service.check(manifest, AdmissionContext.SANDBOX, ctx=_CTX_A)
    allowed_b = app.state.budget_service.check(manifest, AdmissionContext.SANDBOX, ctx=_CTX_B)
    assert allowed_a.allowed is False
    assert allowed_b.allowed is True

    assert app.state.policy_pack_store.list_packs(ctx=_CTX_B) == []
    assert app.state.auth_service.keys.list_keys("tenant-b", ctx=_CTX_B) == []
    assert {k.key_id for k in app.state.auth_service.keys.list_keys("tenant-a", ctx=_CTX_A)} == {
        key_a.key_id
    }


def test_suspended_tenant_cannot_submit_or_authenticate(
    make_manifest: ManifestFactory,
) -> None:
    app = create_app()
    app.state.tenant_admin_service.create_tenant(
        SYSTEM_CONTEXT, tenant_id="tenant-a", name="A"
    )
    app.state.registry_service.create(make_manifest(name="agent-a"), ctx=_CTX_A)
    app.state.tenant_admin_service.suspend_tenant(SYSTEM_CONTEXT, "tenant-a")

    with pytest.raises(TenantSuspendedError):
        app.state.run_service.submit(
            workload="agent-a",
            caller="alice",
            context=AdmissionContext.SANDBOX,
            ctx=_CTX_A,
        )

    key = app.state.auth_service.keys.create("tenant-a", Role.ADMIN, ctx=SYSTEM_CONTEXT)
    with pytest.raises(TenantSuspendedError):
        app.state.auth_service.authenticate_key(key.token)


def test_cross_tenant_http_statuses_are_consistent(
    make_manifest: ManifestFactory,
) -> None:
    app = create_app()
    app.state.registry_service.create(make_manifest(name="agent-a"), ctx=_CTX_A)
    app.state.registry_service.create(make_manifest(name="agent-b"), ctx=_CTX_B)
    client = TestClient(app)

    for endpoint in (
        "/workloads/agent-a",
        "/workloads/agent-a/versions",
        "/workloads/agent-a/admission",
    ):
        assert client.get(endpoint, headers=_headers("tenant-b")).status_code == 404
    assert client.get("/workloads/agent-b", headers=_headers("tenant-a")).status_code == 404
    assert client.get("/workloads/agent-a", headers=_headers("tenant-a")).status_code == 200


def test_tenant_admin_lifecycle_and_quota() -> None:
    from hiveplane.tenancy.models import TenantQuota, TenantStatus

    app = create_app()
    admin = app.state.tenant_admin_service

    created = admin.create_tenant(SYSTEM_CONTEXT, tenant_id="tenant-a", name="A")
    assert created.status is TenantStatus.ACTIVE

    quota = TenantQuota(max_concurrent_runs=2, max_monthly_usd=100.0)
    assert admin.set_quota(SYSTEM_CONTEXT, "tenant-a", quota).quota == quota

    assert admin.suspend_tenant(SYSTEM_CONTEXT, "tenant-a").status is TenantStatus.SUSPENDED
    assert admin.reinstate_tenant(SYSTEM_CONTEXT, "tenant-a").status is TenantStatus.ACTIVE

    with pytest.raises(TenantNotFoundError):
        admin.suspend_tenant(SYSTEM_CONTEXT, "ghost")
