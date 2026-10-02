"""Tests for the registry API (M3-M4)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
from hiveplane.auth.models import Scope
from hiveplane.core.workload import AgentWorkload
from hiveplane.tenancy.models import Role

ManifestFactory = Callable[..., AgentWorkload]


@pytest.fixture
def client() -> TestClient:
    return TestClient(create_app())


def _payload(
    make_manifest: ManifestFactory, name: str = "repo-agent", **kwargs: Any
) -> dict[str, Any]:
    return make_manifest(name, **kwargs).model_dump(by_alias=True, mode="json")


def test_register_and_get_workload(client: TestClient, make_manifest: ManifestFactory) -> None:
    created = client.post("/workloads", json=_payload(make_manifest))

    assert created.status_code == 201
    assert created.json()["name"] == "repo-agent"
    assert created.json()["current_version"] == 1

    fetched = client.get("/workloads/repo-agent")
    assert fetched.status_code == 200
    assert fetched.json()["certification_status"] == "uncertified"


def test_invalid_manifest_returns_422_with_details(client: TestClient) -> None:
    response = client.post("/workloads", json={"kind": "AgentWorkload"})

    assert response.status_code == 422
    detail = response.json()["detail"]
    assert isinstance(detail, list)
    assert any("metadata" in ".".join(str(part) for part in item["loc"]) for item in detail)


def test_duplicate_registration_returns_409(
    client: TestClient, make_manifest: ManifestFactory
) -> None:
    payload = _payload(make_manifest)
    client.post("/workloads", json=payload)

    response = client.post("/workloads", json=payload)

    assert response.status_code == 409


def _auth_app(
    monkeypatch: pytest.MonkeyPatch,
    make_manifest: ManifestFactory,
    *,
    role: Role,
) -> Any:
    monkeypatch.setenv("HIVEPLANE_AUTH__ENABLED", "true")
    from hiveplane.config import get_settings

    get_settings.cache_clear()
    app = create_app()
    app.state.registry_service.create(make_manifest(name="prod-agent"))
    return app


def test_viewer_key_cannot_delete_workload(
    monkeypatch: pytest.MonkeyPatch, make_manifest: ManifestFactory
) -> None:
    app = _auth_app(monkeypatch, make_manifest, role=Role.VIEWER)
    client = TestClient(app)
    viewer = app.state.auth_service.keys.create("default", Role.VIEWER)
    headers = {"Authorization": f"Bearer {viewer.token}"}

    response = client.delete("/workloads/prod-agent", headers=headers)

    assert response.status_code == 403


def test_readonly_scoped_key_cannot_register_tool_or_trigger(
    monkeypatch: pytest.MonkeyPatch, make_manifest: ManifestFactory
) -> None:
    app = _auth_app(monkeypatch, make_manifest, role=Role.ADMIN)
    client = TestClient(app)
    scoped = app.state.auth_service.keys.create(
        "default", Role.ADMIN, scopes=[Scope.FLEET_READ]
    )
    headers = {"Authorization": f"Bearer {scoped.token}"}

    tool = client.post(
        "/tools",
        json={
            "tool_id": "mcp.t.read",
            "name": "mcp.t.read",
            "mcp_server": "test",
            "trust_level": "read_only",
        },
        headers=headers,
    )
    trigger = client.post(
        "/workloads/prod-agent/triggers",
        json={"type": "webhook", "url": "https://example.com/hook"},
        headers=headers,
    )

    assert tool.status_code == 403
    assert trigger.status_code == 403


def test_get_missing_returns_404(client: TestClient) -> None:
    assert client.get("/workloads/missing").status_code == 404


def test_catalog_filters(
    client: TestClient, make_manifest: ManifestFactory
) -> None:
    client.post("/workloads", json=_payload(make_manifest, "repo-agent", owner="platform-team"))
    client.post(
        "/workloads",
        json=_payload(
            make_manifest, "docs-agent", owner="docs-team", team="dx", adapter="langgraph"
        ),
    )

    all_entries = client.get("/workloads").json()
    assert {entry["name"] for entry in all_entries} == {"repo-agent", "docs-agent"}
    assert "manifest" not in all_entries[0]

    filtered = client.get("/workloads", params={"owner": "docs-team"}).json()
    assert [entry["name"] for entry in filtered] == ["docs-agent"]

    by_runtime = client.get("/workloads", params={"runtime": "langgraph"}).json()
    assert [entry["name"] for entry in by_runtime] == ["docs-agent"]

    by_status = client.get("/workloads", params={"certification_status": "uncertified"}).json()
    assert {entry["name"] for entry in by_status} == {"repo-agent", "docs-agent"}


def test_catalog_pagination(
    client: TestClient, make_manifest: ManifestFactory
) -> None:
    for name in ("alpha", "bravo", "charlie"):
        client.post("/workloads", json=_payload(make_manifest, name))

    page = client.get("/workloads", params={"limit": 2, "offset": 1}).json()
    assert [entry["name"] for entry in page] == ["bravo", "charlie"]


def test_update_creates_new_version(
    client: TestClient, make_manifest: ManifestFactory
) -> None:
    client.post("/workloads", json=_payload(make_manifest))
    updated = client.put("/workloads/repo-agent", json=_payload(make_manifest, owner="new-team"))

    assert updated.status_code == 200
    assert updated.json()["current_version"] == 2
    assert updated.json()["owner"] == "new-team"

    versions = client.get("/workloads/repo-agent/versions").json()
    assert [version["version"] for version in versions] == [1, 2]


def test_update_name_mismatch_returns_409(
    client: TestClient, make_manifest: ManifestFactory
) -> None:
    client.post("/workloads", json=_payload(make_manifest))

    response = client.put("/workloads/other", json=_payload(make_manifest))

    assert response.status_code == 409


def test_delete_then_get_returns_404(
    client: TestClient, make_manifest: ManifestFactory
) -> None:
    client.post("/workloads", json=_payload(make_manifest))

    assert client.delete("/workloads/repo-agent").status_code == 204
    assert client.get("/workloads/repo-agent").status_code == 404


def test_dry_run_reports_enforcement_without_persisting(
    client: TestClient, make_manifest: ManifestFactory
) -> None:
    response = client.post("/workloads", json=_payload(make_manifest), params={"dry_run": True})

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["production_admitted"] is False
    assert client.get("/workloads/repo-agent").status_code == 404
