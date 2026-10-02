"""Tests for the public attestation verification API (M35-02)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
from hiveplane.certification.models import Attestation
from hiveplane.certification.signing import generate_keypair, sign_attestation
from hiveplane.core.workload import AgentWorkload
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore

_FIXED_NOW = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)


def _attestation(attestation_id: str = "att-1", **overrides: Any) -> Attestation:
    payload: dict[str, Any] = {
        "attestation_id": attestation_id,
        "workload_id": "repo-agent",
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
            "control_plane_version": "0.2.0",
        },
        "signer": {"identity": "cert@hiveplane", "key_id": "key-1", "signature": "unsigned"},
    }
    payload.update(overrides)
    return Attestation.model_validate(payload)


def _client(
    make_manifest: Callable[..., AgentWorkload],
) -> tuple[TestClient, Any, Any]:
    private_key, public_key = generate_keypair()
    registry = RegistryService(
        InMemoryRegistryStore(),
        clock=lambda: _FIXED_NOW,
        attestation_public_key=public_key,
    )
    registry.create(make_manifest(name="repo-agent"))
    app = create_app(registry_service=registry)
    return TestClient(app), app, private_key


def _store_and_log(app: Any, registry: RegistryService, attestation: Attestation) -> None:
    registry.store_attestation(attestation)
    app.state.transparency_log.append(attestation)


def test_valid_attestation_verifies_publicly_without_auth(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    client, app, private_key = _client(make_manifest)
    signed = sign_attestation(_attestation(), private_key)
    _store_and_log(app, app.state.registry_service, signed)

    response = client.get("/attestations/att-1/verify")

    assert response.status_code == 200
    body = response.json()
    assert body["valid"] is True
    assert body["signer_key_id"] == "key-1"
    assert body["status"] == "certified"
    assert body["log_seq"] == 0
    assert body["chain_valid"] is True
    assert "workload_id" not in body
    assert "eval_summary" not in body


def test_unknown_attestation_returns_404(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    client, _, _ = _client(make_manifest)

    response = client.get("/attestations/missing/verify")

    assert response.status_code == 404


def test_tampered_attestation_reports_invalid(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    client, app, private_key = _client(make_manifest)
    signed = sign_attestation(_attestation(), private_key)
    tampered = signed.model_copy(update={"model_identity": "evil-model"})
    app.state.registry_service.store_attestation(tampered)
    app.state.transparency_log.append(tampered)

    response = client.get("/attestations/att-1/verify")

    assert response.status_code == 200
    assert response.json()["valid"] is False
