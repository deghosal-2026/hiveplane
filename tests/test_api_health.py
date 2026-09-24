"""Tests for the API health surface."""

from __future__ import annotations

from fastapi.testclient import TestClient

from hiveplane.api.app import create_app


def test_healthz_reports_ok() -> None:
    client = TestClient(create_app())

    response = client.get("/healthz")

    assert response.status_code == 200
    assert response.json() == {"status": "ok"}


def test_readyz_reports_ready_when_adapter_attached() -> None:
    app = create_app()
    app.state.adapter = object()
    client = TestClient(app)

    response = client.get("/readyz")

    assert response.status_code == 200
    assert response.json() == {"status": "ready"}


def test_manifest_schema_endpoint() -> None:
    client = TestClient(create_app())

    response = client.get("/manifest/schema")

    assert response.status_code == 200
    assert response.json()["title"] == "AgentWorkload"
