"""API contract tests for the endpoints the operator UI consumes (M22, #89).

These assert the response shapes the UI view models read, so a backend change
that breaks the UI fails here first.
"""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
from hiveplane.core.workload import AgentWorkload

_CATALOG_KEYS = {
    "name",
    "owner",
    "team",
    "runtime",
    "certification_status",
    "current_version",
    "updated_at",
    "last_run_at",
    "failure_count",
    "last_failure_at",
}
_RUN_KEYS = {"id", "workload_id", "caller", "state", "created_at", "updated_at", "cost_usd"}
_STORY_KEYS = {
    "run_id",
    "workload",
    "team",
    "state",
    "context",
    "model_identity",
    "cost_usd",
    "sandbox",
    "sandbox_id",
    "certification_status",
    "attestation_id",
    "trace_id",
    "entries",
}


def _registered_client(make_manifest: Callable[..., AgentWorkload]) -> tuple[TestClient, str]:
    client = TestClient(create_app())
    workload = make_manifest("agent-a")
    response = client.post(
        "/workloads", json=workload.model_dump(by_alias=True, mode="json")
    )
    assert response.status_code == 201
    return client, workload.name


def test_health_endpoints() -> None:
    client = TestClient(create_app())

    assert client.get("/healthz").status_code == 200
    assert client.get("/readyz").status_code == 200


def test_catalog_contract(make_manifest: Callable[..., AgentWorkload]) -> None:
    client, _ = _registered_client(make_manifest)

    entries = client.get("/workloads").json()

    assert isinstance(entries, list)
    assert entries and set(entries[0]) >= _CATALOG_KEYS


def test_runs_and_story_contract(make_manifest: Callable[..., AgentWorkload]) -> None:
    client, name = _registered_client(make_manifest)
    created = client.post(
        "/runs",
        json={"workload": name, "caller": "ui-test", "context": "sandbox", "task": {}},
    )
    assert created.status_code == 201
    run = created.json()
    assert set(run) >= _RUN_KEYS

    listed = client.get("/runs").json()
    assert isinstance(listed, list)
    assert set(listed[0]) >= _RUN_KEYS

    story = client.get(f"/runs/{run['id']}/story")
    assert story.status_code == 200
    assert set(story.json()) >= _STORY_KEYS


def test_runs_filter_rejects_unknown_state() -> None:
    client = TestClient(create_app())

    assert client.get("/runs", params={"state": "bogus"}).status_code == 422


def test_unknown_run_errors() -> None:
    client = TestClient(create_app())

    assert client.get("/runs/nope").status_code == 404
    assert client.get("/runs/nope/story").status_code == 404
    assert client.post("/runs/nope/pause").status_code == 404


def test_approvals_contract_and_unknown_id() -> None:
    client = TestClient(create_app())

    assert client.get("/approvals").json() == []
    assert client.get("/approvals/nope").status_code == 404


def test_certifications_contract() -> None:
    client = TestClient(create_app())

    assert client.get("/certifications").json() == []


def test_spend_contract() -> None:
    client = TestClient(create_app())

    body = client.get("/spend").json()

    assert set(body) == {"by_workload", "by_team"}
    assert body == {"by_workload": [], "by_team": []}


def test_ui_actions_surface_api_error_codes(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    client, name = _registered_client(make_manifest)
    run = client.post(
        "/runs",
        json={"workload": name, "caller": "ui-test", "context": "sandbox", "task": {}},
    ).json()

    # A queued run cannot be resumed; the UI renders the API's 409 as a banner.
    response = client.post(f"/runs/{run['id']}/resume")

    assert response.status_code == 409
    assert "detail" in response.json()
