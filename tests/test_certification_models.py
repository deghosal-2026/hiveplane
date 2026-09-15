"""Tests for certification domain models."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from hiveplane.certification.models import (
    Attestation,
    BenchmarkCorpus,
    BenchmarkTask,
    BenchmarkTaskCheck,
    Certification,
    CertificationEvent,
    CertificationPolicy,
    CertificationStatus,
    CheckType,
    Environment,
    EvalSummary,
    ExpectedOutcome,
    Signer,
    TargetContext,
    Thresholds,
    advance_status,
)

VALID_ATTESTATION = {
    "attestation_id": "att-20260912-xyz789",
    "workload_id": "incident-agent",
    "manifest_version": 5,
    "benchmark_version": "1.0.0",
    "benchmark_run_id": "br-20260912-abc123",
    "corpus_id": "incident-triage-corpus",
    "corpus_version": 3,
    "model_identity": "gpt-4o-2024-08-06",
    "status": "certified",
    "target_context": "production",
    "eval_summary": {
        "pass_rate": 0.917,
        "critical_failures": 0,
        "p95_latency_ms": 24000,
        "tasks_passed": 11,
        "tasks_failed": 1,
    },
    "timestamp": "2026-09-12T10:04:32Z",
    "environment": {
        "sandbox_image": "hiveplane/sandbox:0.2.0",
        "runtime_adapter": "langgraph-adapter:1.3.0",
        "control_plane_version": "0.2.0",
    },
    "signer": {"identity": "cert@hiveplane", "key_id": "hp-key-01", "signature": "base64=="},
    "previous_attestation_id": "att-20260901-lmn456",
}


def test_certification_status_values() -> None:
    assert {status.value for status in CertificationStatus} == {
        "uncertified",
        "provisional",
        "certified",
        "quarantined",
    }


def test_attestation_serialization_is_deterministic() -> None:
    first = Attestation.model_validate(VALID_ATTESTATION)
    second = Attestation.model_validate_json(first.model_dump_json())

    assert first == second
    assert first.model_dump_json() == second.model_dump_json()


def test_attestation_rejects_unknown_field() -> None:
    with pytest.raises(ValidationError):
        Attestation.model_validate({**VALID_ATTESTATION, "extra": 1})


def test_attestation_timestamp_is_timezone_aware() -> None:
    attestation = Attestation.model_validate(VALID_ATTESTATION)

    assert attestation.timestamp == datetime(2026, 9, 12, 10, 4, 32, tzinfo=UTC)


def test_attestation_rejects_naive_timestamp() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        Attestation.model_validate({**VALID_ATTESTATION, "timestamp": "2026-09-12T10:04:32"})


def test_certification_model() -> None:
    certification = Certification(
        certification_id="cert-1",
        workload_id="repo-agent",
        manifest_version=1,
        status=CertificationStatus.CERTIFIED,
        target_context=TargetContext.PRODUCTION,
        benchmark_run_id="br-1",
        thresholds=Thresholds(
            min_pass_rate=0.85, max_critical_failures=0, max_p95_latency_ms=30000
        ),
        eval_summary=EvalSummary(
            pass_rate=0.9,
            critical_failures=0,
            p95_latency_ms=20000,
            tasks_passed=9,
            tasks_failed=1,
        ),
        timestamp=datetime(2026, 9, 12, tzinfo=UTC),
    )

    assert certification.status is CertificationStatus.CERTIFIED


def test_certification_policy_production_not_weaker_than_staging() -> None:
    with pytest.raises(ValidationError, match="production"):
        CertificationPolicy(
            staging=Thresholds(
                min_pass_rate=0.9, max_critical_failures=1, max_p95_latency_ms=60000
            ),
            production=Thresholds(
                min_pass_rate=0.8, max_critical_failures=0, max_p95_latency_ms=30000
            ),
        )


def test_certification_policy_defaults() -> None:
    policy = CertificationPolicy(
        staging=Thresholds(
            min_pass_rate=0.7, max_critical_failures=2, max_p95_latency_ms=60000
        ),
        production=Thresholds(
            min_pass_rate=0.85, max_critical_failures=0, max_p95_latency_ms=30000
        ),
    )

    assert policy.re_cert_interval == 14 * 86400
    assert policy.grace_margin == 0.05


def test_benchmark_task_check_requires_matching_fields() -> None:
    with pytest.raises(ValidationError, match="value"):
        BenchmarkTaskCheck(type=CheckType.EXACT_MATCH, field="severity")


def test_action_audit_check_requires_actions() -> None:
    with pytest.raises(ValidationError, match="action_audit"):
        BenchmarkTaskCheck(type=CheckType.ACTION_AUDIT)


def test_rubric_check_requires_criteria_and_score() -> None:
    with pytest.raises(ValidationError, match="rubric"):
        BenchmarkTaskCheck(type=CheckType.RUBRIC)


def test_custom_check_requires_callable() -> None:
    with pytest.raises(ValidationError, match="custom"):
        BenchmarkTaskCheck(type=CheckType.CUSTOM)


def test_policy_rejects_more_critical_failures_than_staging() -> None:
    with pytest.raises(ValidationError, match="critical"):
        CertificationPolicy(
            staging=Thresholds(
                min_pass_rate=0.7, max_critical_failures=0, max_p95_latency_ms=60000
            ),
            production=Thresholds(
                min_pass_rate=0.85, max_critical_failures=1, max_p95_latency_ms=30000
            ),
        )


def test_benchmark_corpus_requires_unique_task_ids() -> None:
    task = BenchmarkTask(
        id="task-001",
        name="classify severity",
        expected=ExpectedOutcome(outcome="severity: high"),
        check=BenchmarkTaskCheck(type=CheckType.EXACT_MATCH, field="severity", value="high"),
    )

    with pytest.raises(ValidationError, match="unique"):
        BenchmarkCorpus(id="corpus", version=1, tasks=[task, task])


UNC = CertificationStatus.UNCERTIFIED
PROV = CertificationStatus.PROVISIONAL
CERT = CertificationStatus.CERTIFIED
QUAR = CertificationStatus.QUARANTINED

STAGING_PASS = CertificationEvent.STAGING_PASS
PRODUCTION_PASS = CertificationEvent.PRODUCTION_PASS
RECERT_FAIL = CertificationEvent.RECERT_FAIL
DRIFT = CertificationEvent.DRIFT
RECOVER = CertificationEvent.RECOVER


@pytest.mark.parametrize(
    ("current", "event", "expected"),
    [
        (UNC, STAGING_PASS, PROV),
        (PROV, PRODUCTION_PASS, CERT),
        (CERT, PRODUCTION_PASS, CERT),
        (CERT, RECERT_FAIL, QUAR),
        (CERT, DRIFT, QUAR),
        (PROV, RECERT_FAIL, QUAR),
        (QUAR, RECOVER, PROV),
    ],
)
def test_advance_status(
    current: CertificationStatus,
    event: CertificationEvent,
    expected: CertificationStatus,
) -> None:
    assert advance_status(current, event) is expected


def test_advance_status_rejects_invalid_transition() -> None:
    with pytest.raises(ValueError, match="invalid transition"):
        advance_status(CertificationStatus.UNCERTIFIED, CertificationEvent.DRIFT)


def test_eval_summary_ranges() -> None:
    with pytest.raises(ValidationError):
        EvalSummary(
            pass_rate=1.5,
            critical_failures=0,
            p95_latency_ms=1,
            tasks_passed=1,
            tasks_failed=0,
        )


def test_environment_and_signer_are_typed() -> None:
    attestation = Attestation.model_validate(VALID_ATTESTATION)

    assert isinstance(attestation.environment, Environment)
    assert isinstance(attestation.signer, Signer)
