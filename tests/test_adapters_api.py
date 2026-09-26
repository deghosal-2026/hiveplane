"""Tests for the adapter introspection API (M31)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from hiveplane.adapters.stub import StubAdapter
from hiveplane.api.app import create_app


def test_list_adapters_empty_when_none_configured() -> None:
    client = TestClient(create_app())
    assert client.get("/adapters").json() == []
    assert client.get("/adapters/raw-worker").status_code == 404


def test_list_adapters_reports_contract_and_capabilities() -> None:
    app = create_app()
    app.state.adapter_catalog = {"raw-worker": StubAdapter()}
    client = TestClient(app)
    body = client.get("/adapters").json()
    assert body[0]["name"] == "raw-worker"
    assert body[0]["contract_version"] == "2"
    assert body[0]["conformance_version"] == "2"
    assert body[0]["capabilities"]["pause_resume"] is True
    assert client.get("/adapters/raw-worker").json()["name"] == "raw-worker"
