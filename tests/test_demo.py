"""Demo profile seeding (M59-07)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
from hiveplane.demo import DemoSeeder
from hiveplane.tenancy import TenantContext


def _client() -> TestClient:
    return TestClient(create_app())


def test_seed_is_idempotent_and_deterministic() -> None:
    app = create_app()
    seeder = DemoSeeder(
        app.state.tenant_admin_service,
        app.state.registry_service.store,
        app.state.run_store,
        app.state.cost_service,
    )

    first = seeder.seed()
    second = seeder.seed()

    assert first.tenant_ids == second.tenant_ids == ["acme", "beta"]
    assert first.workloads == second.workloads == 6
    assert first.runs == second.runs == 8

    acme = TenantContext(tenant_id="acme")
    beta = TenantContext(tenant_id="beta")
    assert len(app.state.registry_service.store.list_workloads(ctx=acme)) == 3
    assert len(app.state.registry_service.store.list_workloads(ctx=beta)) == 3
    assert len(app.state.run_store.list_runs(ctx=acme)) == 4
    assert len(app.state.run_store.list_runs(ctx=beta)) == 4


def test_seed_api_produces_a_visible_fleet() -> None:
    client = _client()
    headers = {"X-Hiveplane-Tenant": "default"}

    response = client.post("/demo/seed", headers=headers, json={"profile": "default"})
    assert response.status_code == 200
    assert response.json()["workloads"] == 6

    acme = client.get("/workloads", headers={"X-Hiveplane-Tenant": "acme"}).json()
    assert {entry["name"] for entry in acme} == {
        "triage-agent",
        "refund-agent",
        "reconcile-agent",
    }
    beta = client.get("/workloads", headers={"X-Hiveplane-Tenant": "beta"}).json()
    assert {entry["name"] for entry in beta} == {
        "forecast-agent",
        "billing-agent",
        "ops-agent",
    }
