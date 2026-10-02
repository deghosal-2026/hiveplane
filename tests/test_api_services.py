"""Tests for the agent-as-service endpoint (M56-03)."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
from hiveplane.config import get_settings
from hiveplane.core.workload import AgentWorkload
from hiveplane.registry.service import RegistryService
from hiveplane.tenancy.models import Role


def _client(make_manifest: Callable[..., AgentWorkload]) -> TestClient:
    app = create_app()
    registry: RegistryService = app.state.registry_service
    registry.create(
        make_manifest(
            name="agent-1",
            sandbox={
                "enabled": True,
                "resource_caps": {"memory_mb": 128, "cpu_cores": 1.0, "wall_clock_s": 10},
            },
        )
    )
    return TestClient(app)


def test_invoke_serves_a_workload_through_admission(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    client = _client(make_manifest)

    response = client.post(
        "/services/agent-1/invoke", json={"task": {"x": 1}, "context": "sandbox"}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["workload_id"] == "agent-1"
    assert body["context"] == "sandbox"


def test_invoke_refuses_uncertified_production(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    client = _client(make_manifest)

    response = client.post("/services/agent-1/invoke", json={"context": "production"})

    assert response.status_code == 403


def test_invoke_unknown_workload_is_404(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    client = _client(make_manifest)

    response = client.post("/services/ghost/invoke", json={"context": "sandbox"})

    assert response.status_code == 404


def test_invoke_requires_authentication_when_enabled(
    monkeypatch: pytest.MonkeyPatch, make_manifest: Callable[..., AgentWorkload]
) -> None:
    monkeypatch.setenv("HIVEPLANE_AUTH__ENABLED", "true")
    get_settings.cache_clear()
    client = _client(make_manifest)

    unauthenticated = client.post("/services/agent-1/invoke", json={"context": "sandbox"})
    assert unauthenticated.status_code == 401

    key = client.app.state.auth_service.keys.create("default", Role.VIEWER)  # type: ignore[attr-defined]
    authorized = client.post(
        "/services/agent-1/invoke",
        json={"context": "sandbox"},
        headers={"Authorization": f"Bearer {key.token}"},
    )
    assert authorized.status_code == 201
