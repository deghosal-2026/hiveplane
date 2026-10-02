"""API tests for incident mode (M53)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from hiveplane.api.app import create_app


def test_fleet_state_starts_running() -> None:
    client = TestClient(create_app())

    response = client.get("/fleet/state")

    assert response.status_code == 200
    body = response.json()
    assert body["halted"] is False
    assert body["active"] is None
    assert body["history"] == []


def test_pause_and_resume_round_trip() -> None:
    client = TestClient(create_app())

    paused = client.post(
        "/fleet/pause", json={"actor": "alice", "reason": "prod down"}
    )
    assert paused.status_code == 200
    record = paused.json()
    assert record["scope"] == "fleet"
    assert record["actor"] == "alice"
    assert record["resumed_at"] is None

    state = client.get("/fleet/state").json()
    assert state["halted"] is True
    assert state["active"]["incident_id"] == record["incident_id"]

    resumed = client.post("/fleet/resume", json={"actor": "bob"})
    assert resumed.status_code == 200
    assert resumed.json()["resumed_by"] == "bob"

    state = client.get("/fleet/state").json()
    assert state["halted"] is False
    assert state["active"] is None
    assert len(state["history"]) == 1


def test_resume_without_incident_conflicts() -> None:
    client = TestClient(create_app())

    response = client.post("/fleet/resume", json={"actor": "bob"})

    assert response.status_code == 409


def test_pause_scoped_to_tenant() -> None:
    client = TestClient(create_app())

    response = client.post(
        "/fleet/pause",
        json={"actor": "alice", "scope": "tenant", "scope_ref": "acme"},
    )

    assert response.status_code == 200
    assert response.json()["scope"] == "tenant"
    assert response.json()["scope_ref"] == "acme"

    state = client.get("/fleet/state", headers={"X-Hiveplane-Tenant": "acme"}).json()
    assert state["halted"] is True
    assert state["active"]["scope_ref"] == "acme"


def test_fleet_state_hides_other_tenant_active_incident() -> None:
    client = TestClient(create_app())
    client.post(
        "/fleet/pause",
        json={"actor": "alice", "scope": "tenant", "scope_ref": "t2"},
    )

    state = client.get("/fleet/state", headers={"X-Hiveplane-Tenant": "t1"}).json()

    assert state["halted"] is False
    assert state["active"] is None
    assert all(record["scope_ref"] != "t2" for record in state["history"])


def test_create_app_wires_the_halt_gate_into_admission() -> None:
    app = create_app()
    client = TestClient(app)
    client.post("/fleet/pause", json={"actor": "alice", "reason": "incident"})

    admission = app.state.run_service._admission
    assert admission._halt is not None
    assert admission._halt.halted(tenant_id="default", workload="x") is True

