"""Tenant admin API: create/suspend/reinstate/quota (M58-06)."""

from __future__ import annotations

from collections.abc import Callable
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
from hiveplane.api.deps import TENANT_HEADER
from hiveplane.config import get_settings
from hiveplane.core.workload import AgentWorkload
from hiveplane.tenancy import Role
from hiveplane.tenancy.context import SYSTEM_CONTEXT, SYSTEM_TENANT_ID, TenantContext


def _client() -> TestClient:
    return TestClient(create_app())


def _seed_tenant(app: FastAPI, tenant_id: str) -> None:
    app.state.tenant_admin_service.create_tenant(
        SYSTEM_CONTEXT, tenant_id=tenant_id, name=tenant_id.title()
    )


def _run_payload() -> dict[str, object]:
    return {
        "workload": "agent-1",
        "caller": "cli",
        "context": "sandbox",
        "task": {"x": 1},
        "model_identity": "openai/gpt-4o/2024-08-06",
    }


def _auth_client(
    monkeypatch: pytest.MonkeyPatch, tenant_id: str
) -> tuple[TestClient, str]:
    monkeypatch.setenv("HIVEPLANE_AUTH__ENABLED", "true")
    get_settings.cache_clear()
    app = create_app()
    key = app.state.auth_service.keys.create(tenant_id, Role.ADMIN, ctx=SYSTEM_CONTEXT)
    return TestClient(app), key.token


def test_create_tenant_works_in_trusted_local_mode() -> None:
    app = create_app()
    client = TestClient(app)

    created = client.post("/tenants", json={"tenant_id": "acme", "name": "Acme"})

    assert created.status_code == 201
    assert created.json()["status"] == "active"
    listed = client.get("/tenants", headers={TENANT_HEADER: "acme"}).json()
    assert [tenant["tenant_id"] for tenant in listed] == ["acme"]


def test_suspend_reinstate_and_quota_flow() -> None:
    app = create_app()
    _seed_tenant(app, "acme")
    client = TestClient(app)
    headers = {TENANT_HEADER: "acme"}

    quota = client.put("/tenants/acme/quota", json={"max_agents": 5}, headers=headers)
    assert quota.status_code == 200
    assert quota.json()["quota"]["max_agents"] == 5

    suspended = client.post("/tenants/acme/suspend", headers=headers)
    assert suspended.status_code == 200
    assert suspended.json()["status"] == "suspended"

    reinstated = client.post("/tenants/acme/reinstate", headers=headers)
    assert reinstated.status_code == 200
    assert reinstated.json()["status"] == "active"


def test_list_tenants_is_scoped_to_the_acting_tenant() -> None:
    app = create_app()
    _seed_tenant(app, "acme")
    _seed_tenant(app, "globex")
    client = TestClient(app)

    response = client.get("/tenants", headers={TENANT_HEADER: "acme"})

    assert response.status_code == 200
    assert [tenant["tenant_id"] for tenant in response.json()] == ["acme"]


def test_suspend_missing_tenant_returns_404() -> None:
    client = _client()

    response = client.post("/tenants/ghost/suspend", headers={TENANT_HEADER: "ghost"})

    assert response.status_code == 404


def test_suspended_tenant_cannot_submit_runs(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    app = create_app()
    _seed_tenant(app, "acme")
    app.state.registry_service.create(
        make_manifest(name="agent-1"),
        ctx=TenantContext(tenant_id="acme", role=Role.ADMIN),
    )
    client = TestClient(app)
    headers = {TENANT_HEADER: "acme"}

    admitted = client.post("/runs", json=_run_payload(), headers=headers)
    assert admitted.status_code == 201

    client.post("/tenants/acme/suspend", headers=headers)
    refused = client.post("/runs", json=_run_payload(), headers=headers)

    assert refused.status_code == 403


def test_create_tenant_refused_for_tenant_admin_when_auth_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, token = _auth_client(monkeypatch, "acme")
    headers = {"Authorization": f"Bearer {token}", TENANT_HEADER: "acme"}

    response = client.post(
        "/tenants", json={"tenant_id": "other", "name": "Other"}, headers=headers
    )

    assert response.status_code == 403


def test_system_principal_can_create_and_administer_tenants(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, token = _auth_client(monkeypatch, SYSTEM_TENANT_ID)
    system = {"Authorization": f"Bearer {token}"}

    created = client.post("/tenants", json={"tenant_id": "acme", "name": "Acme"}, headers=system)
    assert created.status_code == 201

    suspended = client.post("/tenants/acme/suspend", headers=system)
    assert suspended.status_code == 200
    assert suspended.json()["status"] == "suspended"


def test_tenant_admin_can_suspend_its_own_tenant(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    client, token = _auth_client(monkeypatch, "acme")
    app = cast("FastAPI", client.app)
    _seed_tenant(app, "acme")
    headers = {"Authorization": f"Bearer {token}", TENANT_HEADER: "acme"}

    suspended = client.post("/tenants/acme/suspend", headers=headers)

    assert suspended.status_code == 200
    assert suspended.json()["status"] == "suspended"


def test_tenant_admin_service_is_wired() -> None:
    app = create_app()
    assert app.state.tenant_admin_service is not None
