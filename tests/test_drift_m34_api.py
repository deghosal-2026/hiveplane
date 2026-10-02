"""Drift/quarantine API and dashboard tests (M34)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
from hiveplane.certification.models import TargetContext
from hiveplane.certification.workflow import CertificationCoordinator
from hiveplane.config import get_settings
from hiveplane.core.workload import AgentWorkload
from hiveplane.registry.service import RegistryService
from hiveplane.ui.views import build_cert_dashboard
from test_drift_m34 import _Clock, _coordinator, _setup, _write_corpus


def _client(
    registry: RegistryService, coordinator: CertificationCoordinator | None
) -> TestClient:
    return TestClient(
        create_app(
            registry_service=registry, certification_coordinator=coordinator
        )
    )


def _drifting_summary() -> dict[str, object]:
    return {
        "pass_rate": 0.85,
        "critical_failures": 0,
        "p95_latency_ms": 1000,
        "tasks_passed": 17,
        "tasks_failed": 3,
    }


def test_drift_api_quarantines_and_reinstates(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    corpora_dir = _write_corpus(tmp_path)
    clock = _Clock()
    registry, service, store = _setup(make_manifest, corpora_dir, clock)
    coordinator = _coordinator(registry, service, store, _reference(), corpora_dir, clock)
    coordinator.certify("repo-agent", target_context=TargetContext.STAGING)
    coordinator.certify("repo-agent", target_context=TargetContext.PRODUCTION)
    client = _client(registry, coordinator)

    assert client.get("/drift/schedules").json()
    assert client.get("/drift/due").json() == []
    assert client.get("/drift/expiries").json()[0]["state"] in {"valid", "expiring"}

    stable = client.post(
        "/drift/assess",
        json={
            "workload": "repo-agent",
            "current": {
                "pass_rate": 1.0,
                "critical_failures": 0,
                "p95_latency_ms": 1000,
                "tasks_passed": 20,
                "tasks_failed": 0,
            },
        },
    )
    assert stable.status_code == 200
    assert stable.json()["verdict"] == "stable"

    first = client.post(
        "/drift/assess",
        json={"workload": "repo-agent", "current": _drifting_summary()},
    )
    assert first.json()["verdict"] == "warning"
    second = client.post(
        "/drift/assess",
        json={"workload": "repo-agent", "current": _drifting_summary()},
    )
    assert second.json()["verdict"] == "drifted"

    quarantines = client.get("/quarantines").json()
    assert len(quarantines) == 1
    quarantine_id = quarantines[0]["quarantine_id"]
    assert "drift" in quarantines[0]["reason"]
    assert client.get(f"/quarantines/{quarantine_id}").status_code == 200
    assert client.get("/quarantines/missing").status_code == 404

    reinstated = client.post(
        f"/quarantines/{quarantine_id}/reinstate", json={"operator": "alice"}
    )
    assert reinstated.status_code == 200
    assert reinstated.json()["status"] == "reinstated"

    manual = client.post(
        "/quarantines",
        json={"workload": "repo-agent", "reason": "operator hold", "operator": "bob"},
    )
    assert manual.status_code == 201
    assert manual.json()["reason"] == "operator hold"

    probe = client.post("/drift/probe", json={"workload": "repo-agent"})
    assert probe.status_code == 200
    assert probe.json()["workload"] == "repo-agent"

    unknown = client.post(
        "/drift/assess",
        json={"workload": "nope", "current": _drifting_summary()},
    )
    assert unknown.status_code == 409


def test_drift_api_requires_a_certified_baseline(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    corpora_dir = _write_corpus(tmp_path)
    clock = _Clock()
    registry, service, store = _setup(make_manifest, corpora_dir, clock)
    coordinator = _coordinator(registry, service, store, _reference(), corpora_dir, clock)
    client = _client(registry, coordinator)
    response = client.post(
        "/drift/assess",
        json={"workload": "repo-agent", "current": _drifting_summary()},
    )
    assert response.status_code == 409


def test_drift_api_disabled_returns_503(
    make_manifest: Callable[..., AgentWorkload],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HIVEPLANE_DRIFT__ENABLED", "false")
    get_settings.cache_clear()
    corpora_dir = _write_corpus(tmp_path)
    clock = _Clock()
    registry, _service, _store = _setup(make_manifest, corpora_dir, clock)
    client = _client(registry, None)
    try:
        assert client.get("/drift/due").status_code == 503
    finally:
        get_settings.cache_clear()


def test_dashboard_includes_quarantine_reason() -> None:
    quarantines = [
        {
            "quarantine_id": "quar-1",
            "workload": "repo-agent",
            "timestamp": "2026-09-12T10:00:00Z",
            "reason": "behavioral drift confirmed",
            "severity": "critical",
            "status": "active",
        }
    ]
    view = build_cert_dashboard([], quarantines)
    assert len(view.quarantine_history) == 1
    entry = view.quarantine_history[0]
    assert entry.reason == "behavioral drift confirmed"
    assert entry.severity == "critical"
    assert entry.status == "active"


def _reference() -> object:
    from hiveplane.certification.runner import ReferenceExecutor

    return ReferenceExecutor()
