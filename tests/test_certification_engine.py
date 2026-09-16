"""Tests for certification threshold evaluation (M6, #22)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hiveplane.certification.engine import (
    CertificationEngine,
    passes_threshold,
    workload_policy,
)
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

_FIXED_NOW = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)
_ENV = Environment(
    sandbox_image="hiveplane/sandbox:0.1.0",
    runtime_adapter="raw-worker",
    control_plane_version="0.1.0",
)


def _policy() -> CertificationPolicy:
    return CertificationPolicy(
        staging=Thresholds(
            min_pass_rate=0.70, max_critical_failures=2, max_p95_latency_ms=60000
        ),
        production=Thresholds(
            min_pass_rate=0.85,
            max_critical_failures=0,
            max_p95_latency_ms=30000,
            min_production_runs_survived=50,
        ),
    )


def _summary(
    *,
    pass_rate: float = 1.0,
    critical_failures: int = 0,
    p95_latency_ms: int = 1000,
    tasks_passed: int = 10,
    tasks_failed: int = 0,
) -> EvalSummary:
    return EvalSummary(
        pass_rate=pass_rate,
        critical_failures=critical_failures,
        p95_latency_ms=p95_latency_ms,
        tasks_passed=tasks_passed,
        tasks_failed=tasks_failed,
    )


def _result(summary: EvalSummary) -> BenchmarkResult:
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
            p50_latency_ms=summary.p95_latency_ms,
            p95_latency_ms=summary.p95_latency_ms,
            total_tokens=0,
        ),
    )


def _engine() -> CertificationEngine:
    return CertificationEngine(_policy(), clock=lambda: _FIXED_NOW)


# --------------------------------------------------------------------------- #
# Threshold boundaries
# --------------------------------------------------------------------------- #
def test_pass_rate_exactly_at_threshold_passes() -> None:
    assert passes_threshold(
        _summary(pass_rate=0.85), _policy().production
    ) is True


def test_pass_rate_below_threshold_fails() -> None:
    assert passes_threshold(
        _summary(pass_rate=0.849), _policy().production
    ) is False


def test_critical_failures_at_max_passes() -> None:
    assert passes_threshold(
        _summary(pass_rate=1.0, critical_failures=2), _policy().staging
    ) is True


def test_critical_failures_above_max_fails_even_with_full_pass_rate() -> None:
    assert passes_threshold(
        _summary(pass_rate=1.0, critical_failures=1), _policy().production
    ) is False


def test_p95_latency_exactly_at_budget_passes() -> None:
    assert passes_threshold(
        _summary(p95_latency_ms=60000), _policy().staging
    ) is True


def test_p95_latency_above_budget_fails() -> None:
    assert passes_threshold(
        _summary(p95_latency_ms=60001), _policy().staging
    ) is False


# --------------------------------------------------------------------------- #
# Status transitions
# --------------------------------------------------------------------------- #
def test_uncertified_passing_staging_becomes_provisional() -> None:
    certification = _engine().evaluate(
        _result(_summary(pass_rate=0.80)),
        current_status=CertificationStatus.UNCERTIFIED,
        target_context=TargetContext.STAGING,
    )

    assert certification.status is CertificationStatus.PROVISIONAL
    assert certification.target_context is TargetContext.STAGING


def test_provisional_passing_production_becomes_certified() -> None:
    certification = _engine().evaluate(
        _result(_summary(pass_rate=0.90)),
        current_status=CertificationStatus.PROVISIONAL,
        target_context=TargetContext.PRODUCTION,
        production_runs_survived=50,
    )

    assert certification.status is CertificationStatus.CERTIFIED


def test_provisional_passing_production_defers_until_survived() -> None:
    certification = _engine().evaluate(
        _result(_summary(pass_rate=0.90)),
        current_status=CertificationStatus.PROVISIONAL,
        target_context=TargetContext.PRODUCTION,
        production_runs_survived=49,
    )

    assert certification.status is CertificationStatus.PROVISIONAL


def test_uncertified_failing_staging_stays_uncertified() -> None:
    certification = _engine().evaluate(
        _result(_summary(pass_rate=0.10, critical_failures=5)),
        current_status=CertificationStatus.UNCERTIFIED,
        target_context=TargetContext.STAGING,
    )

    assert certification.status is CertificationStatus.UNCERTIFIED


def test_certified_failing_production_quarantines() -> None:
    certification = _engine().evaluate(
        _result(_summary(pass_rate=0.50, critical_failures=1)),
        current_status=CertificationStatus.CERTIFIED,
        target_context=TargetContext.PRODUCTION,
        production_runs_survived=100,
    )

    assert certification.status is CertificationStatus.QUARANTINED


def test_staging_and_production_thresholds_are_independent() -> None:
    result = _result(_summary(pass_rate=0.80, critical_failures=0))

    staging = _engine().evaluate(
        result,
        current_status=CertificationStatus.UNCERTIFIED,
        target_context=TargetContext.STAGING,
    )
    production = _engine().evaluate(
        result,
        current_status=CertificationStatus.PROVISIONAL,
        target_context=TargetContext.PRODUCTION,
        production_runs_survived=50,
    )

    assert staging.status is CertificationStatus.PROVISIONAL
    assert production.status is CertificationStatus.QUARANTINED


def test_evaluation_is_deterministic() -> None:
    result = _result(_summary(pass_rate=0.90))
    engine = _engine()

    first = engine.evaluate(
        result,
        current_status=CertificationStatus.PROVISIONAL,
        target_context=TargetContext.PRODUCTION,
        production_runs_survived=50,
    )
    second = engine.evaluate(
        result,
        current_status=CertificationStatus.PROVISIONAL,
        target_context=TargetContext.PRODUCTION,
        production_runs_survived=50,
    )

    assert first.model_dump(mode="json") == second.model_dump(mode="json")


def test_certification_records_eval_summary_and_thresholds() -> None:
    certification = _engine().evaluate(
        _result(_summary(pass_rate=0.90, critical_failures=0, p95_latency_ms=5000)),
        current_status=CertificationStatus.PROVISIONAL,
        target_context=TargetContext.PRODUCTION,
        production_runs_survived=50,
    )

    assert certification.eval_summary is not None
    assert certification.eval_summary.pass_rate == pytest.approx(0.90)
    assert certification.thresholds == _policy().production
    assert certification.workload_id == "repo-agent"
    assert certification.benchmark_run_id == "br-1"
    assert certification.attestation_id is None


def test_quarantined_passing_recert_recovers_to_provisional() -> None:
    certification = _engine().evaluate(
        _result(_summary(pass_rate=0.90)),
        current_status=CertificationStatus.QUARANTINED,
        target_context=TargetContext.STAGING,
    )

    assert certification.status is CertificationStatus.PROVISIONAL


def test_workload_policy_overrides_fleet_thresholds() -> None:
    from hiveplane.core.spec import CertificationSpec

    spec = CertificationSpec(
        benchmark_corpus="corpora/x/v1",
        staging_threshold=0.60,
        production_threshold=0.95,
        no_critical_failures=True,
        latency_budget_ms=1234,
    )

    policy = workload_policy(spec, _policy())

    assert policy.staging.min_pass_rate == 0.60
    assert policy.production.min_pass_rate == 0.95
    assert policy.production.max_critical_failures == 0
    assert policy.staging.max_p95_latency_ms == 1234
    assert policy.production.min_production_runs_survived == 50
