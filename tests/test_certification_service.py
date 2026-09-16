"""Tests for the certification service and signed attestations (M6, #23)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PublicKey

from hiveplane.certification.engine import CertificationEngine
from hiveplane.certification.models import (
    BenchmarkAggregate,
    BenchmarkResult,
    CertificationPolicy,
    CertificationStatus,
    Environment,
    EvalSummary,
    TargetContext,
    Thresholds,
)
from hiveplane.certification.service import CertificationService
from hiveplane.certification.signing import generate_keypair, verify_attestation
from hiveplane.core.workload import AgentWorkload
from hiveplane.registry.errors import AttestationAlreadyExistsError
from hiveplane.registry.models import AdmissionContext
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore

_FIXED_NOW = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)
_ENV = Environment(
    sandbox_image="hiveplane/sandbox:0.1.0",
    runtime_adapter="raw-worker",
    control_plane_version="0.1.0",
)

Setup = tuple[CertificationService, RegistryService, Ed25519PublicKey]


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


def _summary(pass_rate: float = 0.90, critical_failures: int = 0) -> EvalSummary:
    tasks_passed = round(pass_rate * 10)
    return EvalSummary(
        pass_rate=pass_rate,
        critical_failures=critical_failures,
        p95_latency_ms=1000,
        tasks_passed=tasks_passed,
        tasks_failed=10 - tasks_passed,
    )


def _result(summary: EvalSummary | None = None) -> BenchmarkResult:
    summary = summary or _summary()
    return BenchmarkResult(
        benchmark_run_id="br-1",
        workload_id="repo-agent",
        manifest_version=1,
        corpus_id="repo-agent-corpus",
        corpus_version=1,
        model_identity="gpt-4o-2024-08-06",
        environment=_ENV,
        started_at=_FIXED_NOW,
        finished_at=_FIXED_NOW,
        tasks=[],
        aggregate=BenchmarkAggregate(
            total=summary.tasks_passed + summary.tasks_failed,
            passed=summary.tasks_passed,
            failed=summary.tasks_failed,
            pass_rate=summary.pass_rate,
            critical_failures=summary.critical_failures,
            p50_latency_ms=1000,
            p95_latency_ms=summary.p95_latency_ms,
            total_tokens=0,
        ),
    )


@pytest.fixture
def setup(
    make_manifest: Callable[..., AgentWorkload],
) -> Setup:
    private_key, public_key = generate_keypair()
    registry = RegistryService(
        InMemoryRegistryStore(),
        clock=lambda: _FIXED_NOW,
        attestation_public_key=public_key,
    )
    registry.create(make_manifest(name="repo-agent"))
    engine = CertificationEngine(_policy(), clock=lambda: _FIXED_NOW)
    counter = {"n": 0}

    def _id() -> str:
        counter["n"] += 1
        return f"att-{counter['n']}"

    service = CertificationService(
        engine,
        registry,
        private_key=private_key,
        identity="certification-service@hiveplane",
        key_id="hp-signing-key-01",
        benchmark_version="1.0.0",
        environment=_ENV,
        clock=lambda: _FIXED_NOW,
        id_factory=_id,
    )
    return service, registry, public_key


def test_certify_builds_signed_attestation(setup: Setup) -> None:
    service, _, _ = setup

    attestation = service.certify(_result(), target_context=TargetContext.STAGING)

    assert attestation.attestation_id == "att-1"
    assert attestation.status is CertificationStatus.PROVISIONAL
    assert attestation.target_context is TargetContext.STAGING
    assert attestation.corpus_id == "repo-agent-corpus"
    assert attestation.corpus_version == 1
    assert attestation.model_identity == "gpt-4o-2024-08-06"
    assert attestation.benchmark_version == "1.0.0"
    assert attestation.environment == _ENV
    assert attestation.eval_summary.pass_rate == pytest.approx(0.90)
    assert attestation.signer.identity == "certification-service@hiveplane"
    assert attestation.signer.signature != "unsigned"


def test_certify_signature_verifies_with_public_key(setup: Setup) -> None:
    service, _, public_key = setup

    attestation = service.certify(_result(), target_context=TargetContext.STAGING)

    assert verify_attestation(attestation, public_key) is True


def test_certify_advances_registry_status(setup: Setup) -> None:
    service, registry, _ = setup

    service.certify(_result(), target_context=TargetContext.STAGING)

    assert registry.get("repo-agent").certification_status is CertificationStatus.PROVISIONAL


def test_certify_stores_attestation_and_reads_it_back_verified(setup: Setup) -> None:
    service, registry, _ = setup

    service.certify(_result(), target_context=TargetContext.STAGING)

    assert registry.get_attestation("att-1").attestation_id == "att-1"


def test_certify_is_append_only(setup: Setup) -> None:
    service, registry, _ = setup
    attestation = service.certify(_result(), target_context=TargetContext.STAGING)

    assert len(registry.list_attestations("repo-agent")) == 1
    with pytest.raises(AttestationAlreadyExistsError):
        registry.store_attestation(attestation)


def test_certify_links_previous_attestation(setup: Setup) -> None:
    service, _, _ = setup

    attestation = service.certify(
        _result(),
        target_context=TargetContext.STAGING,
        previous_attestation_id="att-0",
    )

    assert attestation.previous_attestation_id == "att-0"


def test_certify_production_promotes_to_certified_and_admits(setup: Setup) -> None:
    service, registry, _ = setup

    service.certify(_result(), target_context=TargetContext.STAGING)
    certified = service.certify(
        _result(), target_context=TargetContext.PRODUCTION
    )

    record = registry.get("repo-agent")
    assert certified.status is CertificationStatus.CERTIFIED
    assert record.certification_status is CertificationStatus.CERTIFIED
    assert record.manifest.spec.certification is not None
    assert record.manifest.spec.certification.attestation_id == "att-2"
    assert record.manifest.spec.certification.expires_at is not None
    assert registry.check_admission("repo-agent", AdmissionContext.PRODUCTION).admitted is True


def test_certify_failing_recert_quarantines(setup: Setup) -> None:
    service, registry, _ = setup
    service.certify(_result(), target_context=TargetContext.STAGING)

    failed = service.certify(
        _result(_summary(pass_rate=0.10, critical_failures=3)),
        target_context=TargetContext.STAGING,
    )

    assert failed.status is CertificationStatus.QUARANTINED
    assert registry.get("repo-agent").certification_status is CertificationStatus.QUARANTINED
    assert registry.check_admission("repo-agent", AdmissionContext.STAGING).admitted is False
