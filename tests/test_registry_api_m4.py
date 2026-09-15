"""Tests for the M4 registry API: versions, admission, attestations, tools."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
from hiveplane.certification.models import Attestation
from hiveplane.certification.signing import generate_keypair, sign_attestation
from hiveplane.core.workload import AgentWorkload
from hiveplane.registry.models import AdmissionContext
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore

ManifestFactory = Callable[..., AgentWorkload]
NOW = datetime(2026, 9, 12, tzinfo=UTC)


@pytest.fixture
def keypair() -> tuple[Any, Any]:
    return generate_keypair()


@pytest.fixture
def private_key(keypair: tuple[Any, Any]) -> Any:
    private, _ = keypair
    return private


@pytest.fixture
def service(keypair: tuple[Any, Any]) -> RegistryService:
    _, public = keypair
    return RegistryService(
        InMemoryRegistryStore(), clock=lambda: NOW, attestation_public_key=public
    )


@pytest.fixture
def client(service: RegistryService) -> TestClient:
    return TestClient(create_app(service))


def _payload(
    make_manifest: ManifestFactory, name: str = "repo-agent", **kwargs: Any
) -> dict[str, Any]:
    return make_manifest(name, **kwargs).model_dump(by_alias=True, mode="json")


def _signed(workload: str, private_key: Any) -> Attestation:
    payload: dict[str, Any] = {
        "attestation_id": "att-1",
        "workload_id": workload,
        "manifest_version": 1,
        "benchmark_version": "1.0.0",
        "benchmark_run_id": "br-1",
        "corpus_id": "corpus",
        "corpus_version": 1,
        "model_identity": "gpt-4o-2024-08-06",
        "status": "certified",
        "target_context": "production",
        "eval_summary": {
            "pass_rate": 0.95,
            "critical_failures": 0,
            "p95_latency_ms": 1000,
            "tasks_passed": 19,
            "tasks_failed": 1,
        },
        "timestamp": "2026-09-12T10:00:00Z",
        "environment": {
            "sandbox_image": "img",
            "runtime_adapter": "raw-worker",
            "control_plane_version": "0.1.0",
        },
        "signer": {"identity": "cert@hiveplane", "key_id": "key-1", "signature": "pending"},
    }
    return sign_attestation(Attestation.model_validate(payload), private_key)


def test_version_diff_endpoint(
    client: TestClient, make_manifest: ManifestFactory
) -> None:
    client.post("/workloads", json=_payload(make_manifest))
    client.put("/workloads/repo-agent", json=_payload(make_manifest, owner="new-team"))

    response = client.get(
        "/workloads/repo-agent/versions/diff",
        params={"from_version": 1, "to_version": 2},
    )

    assert response.status_code == 200
    assert response.json()["changed_fields"] == ["metadata"]


def test_admission_endpoint(
    client: TestClient, make_manifest: ManifestFactory
) -> None:
    client.post("/workloads", json=_payload(make_manifest))

    sandbox = client.get(
        "/workloads/repo-agent/admission", params={"context": AdmissionContext.SANDBOX.value}
    )
    production = client.get(
        "/workloads/repo-agent/admission", params={"context": AdmissionContext.PRODUCTION.value}
    )

    assert sandbox.json()["admitted"] is True
    assert production.json()["admitted"] is False


def test_promote_blocked_then_allowed(
    client: TestClient, make_manifest: ManifestFactory
) -> None:
    client.post("/workloads", json=_payload(make_manifest))
    client.put(
        "/workloads/repo-agent",
        json=_payload(
            make_manifest,
            model={
                "strategy": "fixed",
                "identity": {"provider": "openai", "family": "gpt-4o", "version": "2024-08-06"},
            },
        ),
    )

    blocked = client.post("/workloads/repo-agent/promote", json={"version": 2})
    assert blocked.status_code == 409
    assert "spec.model" in blocked.json()["detail"]

    # owner-only change allows promotion
    client.put(
        "/workloads/repo-agent",
        json=_payload(
            make_manifest,
            owner="new-team",
            model={
                "strategy": "fixed",
                "identity": {"provider": "openai", "family": "gpt-4o", "version": "2024-08-06"},
            },
        ),
    )
    allowed = client.post("/workloads/repo-agent/promote", json={"version": 3})
    assert allowed.status_code == 200


def test_register_tool_and_list(client: TestClient) -> None:
    created = client.post(
        "/tools",
        json={
            "tool_id": "mcp.github.read_issue",
            "name": "Read issue",
            "mcp_server": "github-tools",
            "trust_level": "read_only",
        },
    )
    assert created.status_code == 201

    tools = client.get("/tools").json()
    assert [tool["tool_id"] for tool in tools] == ["mcp.github.read_issue"]

    filtered = client.get("/tools", params={"trust_level": "destructive"}).json()
    assert filtered == []


def test_registering_workload_with_unknown_tool_is_rejected(
    client: TestClient, make_manifest: ManifestFactory
) -> None:
    response = client.post(
        "/workloads",
        json=_payload(
            make_manifest,
            tools={"allow": [{"tool_id": "missing", "trust_level": "read_only"}]},
        ),
    )

    assert response.status_code == 422
    assert "missing" in response.json()["detail"]


def test_workload_with_registered_tool_registers(
    client: TestClient, make_manifest: ManifestFactory
) -> None:
    client.post(
        "/tools",
        json={
            "tool_id": "mcp.github.read_issue",
            "name": "Read issue",
            "mcp_server": "github-tools",
            "trust_level": "read_only",
        },
    )
    response = client.post(
        "/workloads",
        json=_payload(
            make_manifest,
            tools={"allow": [{"tool_id": "mcp.github.read_issue", "trust_level": "read_only"}]},
        ),
    )

    assert response.status_code == 201


def test_attestation_endpoints(
    client: TestClient,
    service: RegistryService,
    make_manifest: ManifestFactory,
    private_key: Any,
) -> None:
    client.post("/workloads", json=_payload(make_manifest))
    service.store_attestation(_signed("repo-agent", private_key))

    listed = client.get("/workloads/repo-agent/attestations")
    fetched = client.get("/attestations/att-1")

    assert listed.status_code == 200
    assert len(listed.json()) == 1
    assert fetched.status_code == 200
    assert fetched.json()["attestation_id"] == "att-1"


def test_tampered_attestation_returns_422(
    client: TestClient,
    service: RegistryService,
    make_manifest: ManifestFactory,
    private_key: Any,
) -> None:
    client.post("/workloads", json=_payload(make_manifest))
    tampered = _signed("repo-agent", private_key).model_copy(update={"model_identity": "evil"})
    service._store.add_attestation(tampered)

    response = client.get("/attestations/att-1")

    assert response.status_code == 422


def test_missing_attestation_returns_404(client: TestClient) -> None:
    assert client.get("/attestations/missing").status_code == 404


def test_trigger_endpoints(client: TestClient, make_manifest: ManifestFactory) -> None:
    client.post("/workloads", json=_payload(make_manifest))

    created = client.post(
        "/workloads/repo-agent/triggers",
        json={"type": "cron", "schedule": "0 0 * * *"},
    )
    assert created.status_code == 201

    listed = client.get("/workloads/repo-agent/triggers").json()
    assert len(listed) == 1

    trigger_id = created.json()["trigger_id"]
    assert client.delete(f"/workloads/repo-agent/triggers/{trigger_id}").status_code == 204
    assert client.get("/workloads/repo-agent/triggers").json() == []
