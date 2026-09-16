"""Tests for the run API."""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
from hiveplane.core.workload import AgentWorkload
from hiveplane.registry.service import RegistryService


def _client(make_manifest: Callable[..., AgentWorkload]) -> TestClient:
    app = create_app()
    registry: RegistryService = app.state.registry_service
    workload = make_manifest(
        name="agent-1",
        sandbox={
            "enabled": True,
            "resource_caps": {"memory_mb": 128, "cpu_cores": 1.0, "wall_clock_s": 10},
        },
    )
    registry.create(workload)
    return TestClient(app)


def _payload(context: str = "sandbox") -> dict[str, object]:
    return {
        "workload": "agent-1",
        "caller": "cli",
        "context": context,
        "task": {"x": 1},
        "model_identity": "openai/gpt-4o/2024-08-06",
    }


def test_submit_run_sandbox(make_manifest: Callable[..., AgentWorkload]) -> None:
    client = _client(make_manifest)
    response = client.post("/runs", json=_payload())
    assert response.status_code == 201
    body = response.json()
    assert body["state"] == "queued"
    assert body["context"] == "sandbox"


def test_uncertified_production_is_refused(make_manifest: Callable[..., AgentWorkload]) -> None:
    client = _client(make_manifest)
    response = client.post("/runs", json=_payload(context="production"))
    assert response.status_code == 403


def test_submit_run_unknown_workload_returns_404(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    client = _client(make_manifest)
    payload = _payload()
    payload["workload"] = "missing"
    response = client.post("/runs", json=payload)
    assert response.status_code == 404


def test_get_and_list_runs(make_manifest: Callable[..., AgentWorkload]) -> None:
    client = _client(make_manifest)
    run_id = client.post("/runs", json=_payload()).json()["id"]
    assert client.get(f"/runs/{run_id}").status_code == 200
    assert client.get("/runs", params={"workload": "agent-1"}).json()[0]["id"] == run_id


def test_get_missing_run_returns_404(make_manifest: Callable[..., AgentWorkload]) -> None:
    client = _client(make_manifest)
    assert client.get("/runs/nope").status_code == 404


def test_transition_via_intervention_endpoints(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    client = _client(make_manifest)
    run_id = client.post("/runs", json=_payload()).json()["id"]
    running = client.post(f"/runs/{run_id}/resume")
    assert running.status_code == 409
    assert client.get(f"/runs/{run_id}/events").json()[0]["type"] == "admission"


def test_usage_endpoint_is_empty_initially(make_manifest: Callable[..., AgentWorkload]) -> None:
    client = _client(make_manifest)
    run_id = client.post("/runs", json=_payload()).json()["id"]
    assert client.get(f"/runs/{run_id}/usage").json() == []


def test_tool_call_endpoint_denies_unlisted_tool(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    client = _client(make_manifest)
    run_id = client.post("/runs", json=_payload()).json()["id"]

    response = client.post(f"/runs/{run_id}/tool-calls", json={"tool_id": "mcp.t.read"})

    assert response.status_code == 200
    assert response.json()["outcome"] == "denied"
    assert response.json()["rule"] == "default.deny"


def test_start_queued_run(make_manifest: Callable[..., AgentWorkload]) -> None:
    client = _client(make_manifest)
    run_id = client.post("/runs", json=_payload()).json()["id"]

    response = client.post(f"/runs/{run_id}/start")

    assert response.status_code == 200
    assert response.json()["state"] == "running"


def test_stop_queued_run(make_manifest: Callable[..., AgentWorkload]) -> None:
    client = _client(make_manifest)
    run_id = client.post("/runs", json=_payload()).json()["id"]

    response = client.post(f"/runs/{run_id}/stop")

    assert response.status_code == 200
    assert response.json()["state"] == "cancelled"
