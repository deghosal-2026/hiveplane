"""Tests for the certification store and its Postgres backend (#126)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Engine

from hiveplane.certification.models import (
    Attestation,
    BenchmarkAggregate,
    BenchmarkResult,
    Certification,
    CertificationRecord,
    CertificationStatus,
    Environment,
    EvalSummary,
    Signer,
    TargetContext,
    Thresholds,
)
from hiveplane.certification.signing import generate_keypair, sign_attestation
from hiveplane.certification.store import (
    InMemoryCertificationStore,
    PostgresCertificationStore,
    build_certification_store,
)
from hiveplane.config import Settings
from hiveplane.persistence.base import create_engine_from_settings
from postgres import seed_workload

_NOW = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)
_ENV = Environment(
    sandbox_image="hiveplane/sandbox:0.1.0",
    runtime_adapter="raw-worker",
    control_plane_version="0.1.0",
)


def _record(
    record_id: str = "rec-1",
    *,
    workload: str = "repo-agent",
    status: CertificationStatus = CertificationStatus.CERTIFIED,
) -> CertificationRecord:
    private_key, _ = generate_keypair()
    attestation = sign_attestation(
        Attestation(
            attestation_id=f"att-{record_id}",
            workload_id=workload,
            manifest_version=1,
            benchmark_version="1.0.0",
            benchmark_run_id="br-1",
            corpus_id="corpus",
            corpus_version=1,
            model_identity="gpt-4o-2024-08-06",
            status=status,
            target_context=TargetContext.PRODUCTION,
            eval_summary=EvalSummary(
                pass_rate=0.95,
                critical_failures=0,
                p95_latency_ms=1000,
                tasks_passed=19,
                tasks_failed=1,
            ),
            timestamp=_NOW,
            environment=_ENV,
            signer=Signer(identity="cert@hiveplane", key_id="key-1", signature="unsigned"),
        ),
        private_key,
    )
    certification = Certification(
        certification_id=f"cert-{record_id}",
        workload_id=workload,
        manifest_version=1,
        status=status,
        target_context=TargetContext.PRODUCTION,
        benchmark_run_id="br-1",
        thresholds=Thresholds(
            min_pass_rate=0.85, max_critical_failures=0, max_p95_latency_ms=30000
        ),
        eval_summary=attestation.eval_summary,
        timestamp=_NOW,
        attestation_id=attestation.attestation_id,
    )
    result = BenchmarkResult(
        benchmark_run_id="br-1",
        workload_id=workload,
        manifest_version=1,
        corpus_id="corpus",
        corpus_version=1,
        model_identity="gpt-4o-2024-08-06",
        environment=_ENV,
        started_at=_NOW,
        finished_at=_NOW,
        tasks=[],
        aggregate=BenchmarkAggregate(
            total=20,
            passed=19,
            failed=1,
            pass_rate=0.95,
            critical_failures=0,
            p50_latency_ms=500,
            p95_latency_ms=1000,
            total_tokens=0,
        ),
    )
    return CertificationRecord(
        record_id=record_id,
        certification=certification,
        attestation=attestation,
        benchmark_result=result,
    )


def test_builder_defaults_to_in_memory() -> None:
    assert isinstance(build_certification_store(Settings()), InMemoryCertificationStore)


def test_builder_selects_postgres() -> None:
    settings = Settings.model_validate({"execution": {"store": "postgres"}})
    assert isinstance(build_certification_store(settings), PostgresCertificationStore)


def test_in_memory_round_trip() -> None:
    store = InMemoryCertificationStore()
    record = _record()
    store.add(record)

    assert store.get("rec-1") is not None
    assert store.list(workload="repo-agent")[0].record_id == "rec-1"
    assert store.list(status=CertificationStatus.QUARANTINED) == []


def test_postgres_round_trip(pg_engine: Engine) -> None:
    store = PostgresCertificationStore(pg_engine)
    seed_workload(pg_engine, name="repo-agent")
    store.clear()
    record = _record()
    store.add(record)

    fetched = store.get("rec-1")
    assert fetched is not None
    assert fetched.attestation.attestation_id == record.attestation.attestation_id
    assert [item.record_id for item in store.list(workload="repo-agent")] == ["rec-1"]
    assert store.list(status=CertificationStatus.QUARANTINED) == []
    assert store.get("missing") is None

    store.add(record)
    assert [item.record_id for item in store.list(limit=1)] == ["rec-1"]
    assert store.list(offset=1) == []


def test_postgres_record_survives_new_store_instance(pg_engine: Engine) -> None:
    store = PostgresCertificationStore(pg_engine)
    seed_workload(pg_engine, name="repo-agent")
    store.clear()
    store.add(_record("rec-restart"))

    fresh_engine = create_engine_from_settings()
    try:
        reopened = PostgresCertificationStore(fresh_engine)
        assert reopened.get("rec-restart") is not None
    finally:
        fresh_engine.dispose()
