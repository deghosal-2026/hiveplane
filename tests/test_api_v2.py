"""Tests for API v2 pagination and the error model (M56-01)."""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
from hiveplane.core.workload import AgentWorkload


def _client(make_manifest: Callable[..., AgentWorkload]) -> TestClient:
    app = create_app()
    app.state.registry_service.create(make_manifest(name="agent-1"))
    return TestClient(app)


def test_error_envelope_and_request_id() -> None:
    client = TestClient(create_app())

    response = client.get("/definitely-missing")

    assert response.status_code == 404
    error = response.json()["error"]
    assert error["code"] == 404
    assert error["message"]
    assert error["request_id"]
    assert response.headers["X-Request-ID"] == error["request_id"]


def test_incoming_request_id_is_echoed() -> None:
    client = TestClient(create_app())

    response = client.get("/healthz", headers={"X-Request-ID": "trace-123"})

    assert response.headers["X-Request-ID"] == "trace-123"


def test_validation_error_uses_the_envelope() -> None:
    client = TestClient(create_app())

    response = client.post("/runs", json={})

    assert response.status_code == 422
    assert response.json()["error"]["message"] == "request validation failed"
    assert isinstance(response.json()["detail"], list)


def test_v2_version() -> None:
    client = TestClient(create_app())

    body = client.get("/v2/version").json()

    assert body["api_version"] == "v2"
    assert body["version"]


def test_v2_runs_are_paginated(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    client = _client(make_manifest)
    for _ in range(2):
        assert (
            client.post(
                "/runs", json={"workload": "agent-1", "caller": "cli", "context": "sandbox"}
            ).status_code
            == 201
        )

    first = client.get("/v2/runs", params={"limit": 1}).json()
    assert len(first["items"]) == 1
    assert first["count"] == 2
    assert first["next_cursor"] == "1"

    second = client.get(
        "/v2/runs", params={"limit": 1, "cursor": first["next_cursor"]}
    ).json()
    assert len(second["items"]) == 1
    assert second["next_cursor"] is None
