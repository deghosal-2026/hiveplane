"""End-to-end certification admission tests (M6, #24).

Uses the real registry, certification service, and admission pipeline to prove
forged attestations are rejected and a model swap is blocked at admission.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from hiveplane.certification.engine import CertificationEngine
from hiveplane.certification.models import (
    BenchmarkAggregate,
    BenchmarkResult,
    CertificationPolicy,
    Environment,
    TargetContext,
    Thresholds,
)
from hiveplane.certification.service import CertificationService
from hiveplane.certification.signing import generate_keypair
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.admission import AdmissionPipeline
from hiveplane.execution.gates import (
    ManifestSandboxGate,
    PermissivePolicyGate,
    RegistryCertificationGate,
    UnlimitedBudgetGate,
)
from hiveplane.execution.models import AdmissionOutcome
from hiveplane.registry.errors import AttestationVerificationError
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore

_FIXED_NOW = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)
_ENV = Environment(
    sandbox_image="hiveplane/sandbox:0.1.0",
    runtime_adapter="raw-worker",
    control_plane_version="0.1.0",
)
_BOUND_MODEL = "gpt-4o-2024-08-06"


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


def _result() -> BenchmarkResult:
    return BenchmarkResult(
        benchmark_run_id="br-1",
        workload_id="repo-agent",
        manifest_version=1,
        corpus_id="repo-agent-corpus",
        corpus_version=1,
        model_identity=_BOUND_MODEL,
        environment=_ENV,
        started_at=_FIXED_NOW,
        finished_at=_FIXED_NOW,
        tasks=[],
        aggregate=BenchmarkAggregate(
            total=10,
            passed=10,
            failed=0,
            pass_rate=0.95,
            critical_failures=0,
            p50_latency_ms=1000,
            p95_latency_ms=1000,
            total_tokens=0,
        ),
    )


def _run(model: str | None) -> Run:
    return Run(
        id="run-1",
        workload_id="repo-agent",
        caller="cli",
        state=RunState.QUEUED,
        model_identity=model,
        created_at=_FIXED_NOW,
        updated_at=_FIXED_NOW,
        context=AdmissionContext.PRODUCTION,
    )


Certified = tuple[RegistryService, AdmissionPipeline, CertificationService]


@pytest.fixture
def certified(make_manifest: Callable[..., AgentWorkload]) -> Certified:
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
        environment=_ENV,
        clock=lambda: _FIXED_NOW,
        id_factory=_id,
    )
    service.certify(_result(), target_context=TargetContext.STAGING)
    service.certify(_result(), target_context=TargetContext.PRODUCTION)
    pipeline = AdmissionPipeline(
        RegistryCertificationGate(registry),
        PermissivePolicyGate(lambda: _FIXED_NOW),
        UnlimitedBudgetGate(),
        ManifestSandboxGate(),
        clock=lambda: _FIXED_NOW,
    )
    return registry, pipeline, service


def test_matching_model_is_admitted(certified: Certified) -> None:
    registry, pipeline, _ = certified

    result = pipeline.check(_run(_BOUND_MODEL), registry.get("repo-agent").manifest)

    assert result.outcome is AdmissionOutcome.ADMITTED


def test_model_swap_is_blocked_at_admission(certified: Certified) -> None:
    registry, pipeline, _ = certified

    result = pipeline.check(_run("claude-3-5-sonnet"), registry.get("repo-agent").manifest)

    assert result.outcome is AdmissionOutcome.REFUSED
    assert any(check.step == "model_identity" and not check.passed for check in result.checks)


def test_forged_attestation_blocks_production_admission(certified: Certified) -> None:
    registry, _, _ = certified
    stored = registry.get_attestation("att-2")
    registry._store.add_attestation(
        stored.model_copy(update={"model_identity": "evil-model"})
    )

    assert registry.check_admission("repo-agent", AdmissionContext.PRODUCTION).admitted is False


def test_unsigned_attestation_rejected_on_read(certified: Certified) -> None:
    registry, _, _ = certified
    stored = registry.get_attestation("att-2")
    unsigned = stored.signer.model_copy(update={"signature": "unsigned"})
    registry._store.add_attestation(stored.model_copy(update={"signer": unsigned}))

    with pytest.raises(AttestationVerificationError):
        registry.get_attestation("att-2")


def test_attestation_records_certified_status_and_model(certified: Certified) -> None:
    registry, _, _ = certified

    attestation = registry.get_attestation("att-2")

    assert attestation.model_identity == _BOUND_MODEL
    assert attestation.status.value == "certified"
    assert attestation.target_context is TargetContext.PRODUCTION


@pytest.fixture
def certified_with_provenance(
    make_manifest: Callable[..., AgentWorkload],
) -> Certified:
    private_key, public_key = generate_keypair()
    registry = RegistryService(
        InMemoryRegistryStore(),
        clock=lambda: _FIXED_NOW,
        attestation_public_key=public_key,
        bundle_signing_key=private_key,
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
        environment=_ENV,
        clock=lambda: _FIXED_NOW,
        id_factory=_id,
    )
    service.certify(_result(), target_context=TargetContext.STAGING)
    service.certify(_result(), target_context=TargetContext.PRODUCTION)
    pipeline = AdmissionPipeline(
        RegistryCertificationGate(registry),
        PermissivePolicyGate(lambda: _FIXED_NOW),
        UnlimitedBudgetGate(),
        ManifestSandboxGate(),
        clock=lambda: _FIXED_NOW,
    )
    return registry, pipeline, service


def test_verified_provenance_admits_production(
    certified_with_provenance: Certified,
) -> None:
    registry, _, _ = certified_with_provenance

    assert registry.get("repo-agent").bundle is not None
    assert registry.check_admission("repo-agent", AdmissionContext.PRODUCTION).admitted is True


def test_tampered_bundle_is_refused_production_admission(
    certified_with_provenance: Certified,
) -> None:
    registry, _, _ = certified_with_provenance
    record = registry.get("repo-agent")
    assert record.bundle is not None
    tampered = record.bundle.model_copy(update={"bundle_digest": "deadbeef"})
    registry._store.save_workload(record.model_copy(update={"bundle": tampered}))

    assert registry.check_admission("repo-agent", AdmissionContext.PRODUCTION).admitted is False


def test_swapped_manifest_identity_is_refused_production_admission(
    certified_with_provenance: Certified,
) -> None:
    registry, _, _ = certified_with_provenance
    record = registry.get("repo-agent")
    assert record.bundle is not None
    # Keep the bundle but claim a different entrypoint in the current manifest.
    runtime = record.manifest.spec.runtime.model_copy(update={"entrypoint": "examples.evil:run"})
    swapped_manifest = record.manifest.model_copy(
        update={"spec": record.manifest.spec.model_copy(update={"runtime": runtime})}
    )
    registry._store.save_workload(
        record.model_copy(update={"manifest": swapped_manifest})
    )

    assert registry.check_admission("repo-agent", AdmissionContext.PRODUCTION).admitted is False


def test_missing_bundle_is_refused_production_admission(
    certified_with_provenance: Certified,
) -> None:
    registry, _, _ = certified_with_provenance
    record = registry.get("repo-agent")
    registry._store.save_workload(record.model_copy(update={"bundle": None}))

    assert registry.check_admission("repo-agent", AdmissionContext.PRODUCTION).admitted is False


def test_unresolvable_bundle_key_is_refused(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    private_key, public_key = generate_keypair()
    registry = RegistryService(
        InMemoryRegistryStore(),
        clock=lambda: _FIXED_NOW,
        attestation_public_key=public_key,
        bundle_signing_key=private_key,
        key_resolver=lambda _key_id: None,
    )
    registry.create(make_manifest(name="repo-agent"))
    engine = CertificationEngine(_policy(), clock=lambda: _FIXED_NOW)
    service = CertificationService(
        engine,
        registry,
        private_key=private_key,
        environment=_ENV,
        clock=lambda: _FIXED_NOW,
    )
    service.certify(_result(), target_context=TargetContext.STAGING)
    service.certify(_result(), target_context=TargetContext.PRODUCTION)

    assert registry.check_admission("repo-agent", AdmissionContext.PRODUCTION).admitted is False


def test_require_admission_succeeds_when_provenance_verifies(
    certified_with_provenance: Certified,
) -> None:
    registry, _, _ = certified_with_provenance

    decision = registry.require_admission("repo-agent", AdmissionContext.PRODUCTION)

    assert decision.admitted is True
