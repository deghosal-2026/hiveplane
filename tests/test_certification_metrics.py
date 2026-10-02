"""Tests for certification and attestation metrics (M20, #51)."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from hiveplane.certification.models import BenchmarkTask, TargetContext
from hiveplane.certification.runner import TaskExecution
from hiveplane.certification.service import CertificationService
from hiveplane.certification.signing import generate_keypair, sign_attestation
from hiveplane.certification.store import InMemoryCertificationStore
from hiveplane.certification.workflow import CertificationCoordinator
from hiveplane.core.workload import AgentWorkload
from hiveplane.registry.errors import AttestationVerificationError
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore
from metrics import RecordingMetrics
from test_attestation_signing import _attestation
from test_certification_workflow import _ENV, _TASKS, _setup, _write_corpus


class _FailingExecutor:
    """A task executor that fails every check."""

    def execute(self, task: BenchmarkTask) -> TaskExecution:
        return TaskExecution(output={}, latency_ms=1)


def test_certification_records_status_and_duration(
    make_manifest: Callable[..., AgentWorkload],
    fleet_metrics: RecordingMetrics,
    tmp_path: Path,
) -> None:
    corpora_dir = _write_corpus(tmp_path / "corpora", _TASKS)
    _, _, coordinator = _setup(make_manifest, corpora_dir)

    coordinator.certify("repo-agent", target_context=TargetContext.STAGING)

    recorded = fleet_metrics.called("record_certification")
    assert len(recorded) == 1
    assert recorded[0]["workload"] == "repo-agent"
    assert recorded[0]["team"] == "platform"
    assert recorded[0]["status"] in ("provisional", "certified")
    assert recorded[0]["duration_seconds"] >= 0.0


def test_regression_is_recorded_on_failed_recert(
    make_manifest: Callable[..., AgentWorkload],
    fleet_metrics: RecordingMetrics,
    tmp_path: Path,
) -> None:
    corpora_dir = _write_corpus(tmp_path / "corpora", _TASKS)
    registry, service, coordinator = _setup(make_manifest, corpora_dir)
    coordinator.certify("repo-agent", target_context=TargetContext.STAGING)
    failing = _coordinator(registry, service, corpora_dir)

    failing.certify("repo-agent", target_context=TargetContext.STAGING)

    assert fleet_metrics.called("record_regression") == [{"workload": "repo-agent"}]


def _coordinator(
    registry: RegistryService,
    service: CertificationService,
    corpora_dir: Path,
) -> CertificationCoordinator:
    return CertificationCoordinator(
        registry,
        service,
        InMemoryCertificationStore(),
        executor=_FailingExecutor(),
        corpora_dir=corpora_dir,
        environment=_ENV,
    )


def _seed_workload(
    make_manifest: Callable[..., AgentWorkload], store: InMemoryRegistryStore
) -> None:
    from datetime import UTC, datetime

    from hiveplane.certification.models import CertificationStatus
    from hiveplane.registry.models import WorkloadRecord

    now = datetime(2026, 9, 25, tzinfo=UTC)
    manifest = make_manifest(name="repo-agent")
    store.save_workload(
        WorkloadRecord(
            name=manifest.name,
            manifest=manifest,
            current_version=1,
            certification_status=CertificationStatus.CERTIFIED,
            owner=manifest.owner,
            team=manifest.team,
            runtime=manifest.spec.runtime.adapter,
            created_at=now,
            updated_at=now,
        )
    )


def test_get_attestation_records_verified(
    make_manifest: Callable[..., AgentWorkload], fleet_metrics: RecordingMetrics
) -> None:
    private_key, public_key = generate_keypair()
    store = InMemoryRegistryStore()
    _seed_workload(make_manifest, store)
    registry = RegistryService(store, attestation_public_key=public_key)
    store.add_attestation(sign_attestation(_attestation(), private_key))

    registry.get_attestation("att-1")

    assert fleet_metrics.called("record_attestation_verification") == [
        {"workload": "repo-agent", "result": "verified"}
    ]


def test_failed_attestation_verification_is_recorded(
    make_manifest: Callable[..., AgentWorkload],
    fleet_metrics: RecordingMetrics,
) -> None:
    private_key, _ = generate_keypair()
    _, other_public = generate_keypair()
    store = InMemoryRegistryStore()
    _seed_workload(make_manifest, store)
    registry = RegistryService(store, attestation_public_key=other_public)
    store.add_attestation(sign_attestation(_attestation(), private_key))

    with pytest.raises(AttestationVerificationError):
        registry.get_attestation("att-1")

    assert fleet_metrics.called("record_attestation_verification") == [
        {"workload": "repo-agent", "result": "failed"}
    ]
