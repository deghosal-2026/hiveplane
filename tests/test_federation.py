"""Federation of remote planes (M59-08, stretch: behind a feature flag)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
from hiveplane.config import get_settings
from hiveplane.federation import FederationService, InMemoryRemotePlaneStore

_NOW = datetime(2026, 9, 25, tzinfo=UTC)


def _clock() -> datetime:
    return _NOW


def test_federation_disabled_returns_503() -> None:
    client = TestClient(create_app())
    response = client.get("/federation/planes")
    assert response.status_code == 503


def test_federation_register_and_aggregate(monkeypatch) -> None:  # type: ignore[no-untyped-def]
    monkeypatch.setenv("HIVEPLANE_FEDERATION__ENABLED", "true")
    get_settings.cache_clear()
    app = create_app()
    client = TestClient(app)
    headers = {"X-Hiveplane-Tenant": "default"}

    created = client.post(
        "/federation/planes",
        headers=headers,
        json={
            "plane_id": "edge-1",
            "name": "Edge One",
            "base_url": "https://edge-1.example.com",
            "workload_count": 3,
        },
    )
    assert created.status_code == 201

    again = client.post(
        "/federation/planes",
        headers=headers,
        json={
            "plane_id": "edge-1",
            "name": "Edge One (updated)",
            "base_url": "https://edge-1.example.com",
            "workload_count": 4,
        },
    )
    assert again.status_code == 201

    planes = client.get("/federation/planes", headers=headers).json()
    assert [plane["plane_id"] for plane in planes] == ["edge-1"]
    assert planes[0]["name"] == "Edge One (updated)"

    aggregate = client.get("/federation/aggregate", headers=headers).json()
    assert aggregate["plane_count"] == 1
    assert aggregate["total_workloads"] == 4
    assert aggregate["entries"][0]["plane_id"] == "edge-1"


def test_register_is_idempotent_and_keeps_registration_time() -> None:
    service = FederationService(InMemoryRemotePlaneStore(), clock=_clock)

    first = service.register(
        plane_id="edge-1", name="Edge", base_url="https://e", workload_count=1
    )
    second = service.register(
        plane_id="edge-1", name="Edge", base_url="https://e", workload_count=2
    )

    assert first.registered_at == second.registered_at == _NOW
    assert service.aggregate().total_workloads == 2
