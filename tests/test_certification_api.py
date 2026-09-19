"""Tests for the certification REST API (M7, #25)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import yaml
from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
from hiveplane.certification.engine import CertificationEngine
from hiveplane.certification.models import (
    CertificationPolicy,
    Environment,
    Thresholds,
)
from hiveplane.certification.runner import ReferenceExecutor
from hiveplane.certification.service import CertificationService
from hiveplane.certification.signing import generate_keypair
from hiveplane.certification.store import InMemoryCertificationStore
from hiveplane.certification.workflow import CertificationCoordinator
from hiveplane.config import get_settings
from hiveplane.core.workload import AgentWorkload
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore

_FIXED_NOW = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)
_ENV = Environment(
    sandbox_image="hiveplane/sandbox:0.1.0",
    runtime_adapter="raw-worker",
    control_plane_version="0.1.0",
)
_TASKS: list[dict[str, Any]] = [
    {
        "id": "t1",
        "name": "classify low risk",
        "check": {"type": "exact_match", "field": "risk", "value": "low"},
    }
]


def _policy() -> CertificationPolicy:
    return CertificationPolicy(
        staging=Thresholds(
            min_pass_rate=0.70, max_critical_failures=2, max_p95_latency_ms=60000
        ),
        production=Thresholds(
            min_pass_rate=0.85,
            max_critical_failures=0,
            max_p95_latency_ms=30000,
            min_production_runs_survived=0,
        ),
    )


def _client(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> TestClient:
    (tmp_path / "corpus.yaml").write_text(
        yaml.safe_dump({"id": "demo-corpus", "version": 1, "tasks": _TASKS}),
        encoding="utf-8",
    )
    private_key, public_key = generate_keypair()
    registry = RegistryService(
        InMemoryRegistryStore(),
        clock=lambda: _FIXED_NOW,
        attestation_public_key=public_key,
    )
    registry.create(
        make_manifest(
            name="repo-agent",
            certification={
                "benchmark_corpus": "corpus.yaml",
                "staging_threshold": 0.8,
                "production_threshold": 0.9,
            },
        )
    )
    engine = CertificationEngine(_policy(), clock=lambda: _FIXED_NOW)
    counter = {"n": 0}

    def _id() -> str:
        counter["n"] += 1
        return f"att-{counter['n']}"

    service = CertificationService(
        engine,
        registry,
        private_key=private_key,
        environment=_ENV,
        clock=lambda: _FIXED_NOW,
        id_factory=_id,
    )
    coordinator = CertificationCoordinator(
        registry,
        service,
        InMemoryCertificationStore(),
        executor=ReferenceExecutor(),
        corpora_dir=tmp_path,
        environment=_ENV,
        clock=lambda: _FIXED_NOW,
    )
    app = create_app(registry_service=registry, certification_coordinator=coordinator)
    return TestClient(app)


def test_post_certification_returns_status_and_attestation(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    client = _client(make_manifest, tmp_path)

    response = client.post(
        "/certifications", json={"workload": "repo-agent", "target_context": "staging"}
    )

    assert response.status_code == 201
    body = response.json()
    assert body["certification"]["status"] == "provisional"
    assert body["attestation"]["attestation_id"] == "att-1"
    assert body["benchmark_result"]["aggregate"]["total"] == 1


def test_post_certification_unknown_workload_returns_404(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    client = _client(make_manifest, tmp_path)

    response = client.post(
        "/certifications", json={"workload": "missing", "target_context": "staging"}
    )

    assert response.status_code == 404


def test_get_and_list_certifications(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    client = _client(make_manifest, tmp_path)
    created = client.post(
        "/certifications", json={"workload": "repo-agent", "target_context": "staging"}
    ).json()
    certification_id = created["record_id"]

    fetched = client.get(f"/certifications/{certification_id}")
    listed = client.get("/certifications", params={"workload": "repo-agent"})

    assert fetched.status_code == 200
    assert fetched.json()["attestation"]["attestation_id"] == "att-1"
    assert listed.status_code == 200
    assert len(listed.json()) == 1


def test_get_missing_certification_returns_404(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    client = _client(make_manifest, tmp_path)

    assert client.get("/certifications/nope").status_code == 404


def test_compare_certifications(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    client = _client(make_manifest, tmp_path)
    first = client.post(
        "/certifications", json={"workload": "repo-agent", "target_context": "staging"}
    ).json()
    second = client.post(
        "/certifications", json={"workload": "repo-agent", "target_context": "production"}
    ).json()

    response = client.get(
        "/certifications/compare/"
        f"{first['record_id']}/"
        f"{second['record_id']}"
    )

    assert response.status_code == 200
    body = response.json()
    assert body["blocked"] is False
    assert body["before_attestation_id"] == "att-1"
    assert body["after_attestation_id"] == "att-2"


def test_default_app_refuses_certification_without_executor(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path, monkeypatch: Any
) -> None:
    (tmp_path / "corpus.yaml").write_text(
        yaml.safe_dump({"id": "demo-corpus", "version": 1, "tasks": _TASKS}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HIVEPLANE_CERTIFICATION__CORPORA_DIR", str(tmp_path))
    get_settings.cache_clear()
    app = create_app()
    registry: RegistryService = app.state.registry_service
    registry.create(
        make_manifest(name="repo-agent", certification={"benchmark_corpus": "corpus.yaml"})
    )
    client = TestClient(app)

    response = client.post(
        "/certifications", json={"workload": "repo-agent", "target_context": "staging"}
    )

    assert response.status_code == 503


def test_reference_executor_opt_in_allows_certification(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path, monkeypatch: Any
) -> None:
    (tmp_path / "corpus.yaml").write_text(
        yaml.safe_dump({"id": "demo-corpus", "version": 1, "tasks": _TASKS}),
        encoding="utf-8",
    )
    monkeypatch.setenv("HIVEPLANE_CERTIFICATION__CORPORA_DIR", str(tmp_path))
    monkeypatch.setenv("HIVEPLANE_CERTIFICATION__EXECUTOR", "reference")
    get_settings.cache_clear()
    app = create_app()
    registry: RegistryService = app.state.registry_service
    registry.create(
        make_manifest(name="repo-agent", certification={"benchmark_corpus": "corpus.yaml"})
    )
    client = TestClient(app)

    response = client.post(
        "/certifications", json={"workload": "repo-agent", "target_context": "staging"}
    )

    assert response.status_code == 201


def test_start_certification_forwards_model_identity() -> None:
    from hiveplane.api.certifications import (
        CertificationRequest,
        start_certification,
    )

    captured: dict[str, Any] = {}
    sentinel = object()

    class _FakeCoordinator:
        def certify(
            self,
            workload: str,
            *,
            target_context: Any,
            corpus_ref: Any,
            model_identity: str | None = None,
        ) -> Any:
            captured["workload"] = workload
            captured["model_identity"] = model_identity
            return sentinel

    request = CertificationRequest(
        workload="repo-agent", model_identity="openai/gpt-4o/2024-08-06"
    )
    result = start_certification(request, _FakeCoordinator())  # type: ignore[arg-type]

    assert result is sentinel
    assert captured["workload"] == "repo-agent"
    assert captured["model_identity"] == "openai/gpt-4o/2024-08-06"
