"""Tests for the agent health model, SLO/error budget, and burn throttle (M42)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.health.models import (
    BurnThroughAction,
    HealthStatus,
    Objective,
    ObjectiveStatus,
    SloTarget,
)
from hiveplane.health.service import HealthService, compute_health

_NOW = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)


def _run(
    run_id: str,
    *,
    state: RunState,
    offset_seconds: int = 0,
    workload: str = "repo-agent",
) -> Run:
    when = _NOW - timedelta(seconds=offset_seconds)
    return Run(
        id=run_id,
        workload_id=workload,
        caller="cli",
        state=state,
        context=AdmissionContext.PRODUCTION,
        created_at=when,
        updated_at=when,
        started_at=when,
        finished_at=when,
    )


def _service(
    runs: list[Run],
    *,
    quality: float | None = None,
    drift: str = "clean",
    breaker_open: bool = False,
    status: str = "certified",
    quarantine: Callable[[str, str], None] | None = None,
) -> HealthService:
    return HealthService(
        run_history=lambda _workload: runs,
        quality_lookup=lambda _workload: quality,
        drift_lookup=lambda _workload: drift,
        breaker_lookup=lambda _workload: breaker_open,
        status_lookup=lambda _workload: status,
        slo_lookup=lambda _workload: SloTarget(
            availability_target=0.9, quality_target=0.8, window_seconds=86400
        ),
        workload_lookup=lambda: ["repo-agent"],
        clock=lambda: _NOW,
        min_runs_for_score=1,
        quarantine=quarantine,
    )


def test_failure_rate_and_readiness() -> None:
    runs = [
        _run("r1", state=RunState.COMPLETED, offset_seconds=10),
        _run("r2", state=RunState.FAILED, offset_seconds=20),
    ]
    service = _service(runs)

    health = service.workload("repo-agent")

    assert health.terminal_runs == 2
    assert health.failed_runs == 1
    assert health.failure_rate == 0.5
    assert health.readiness is True
    assert health.status is HealthStatus.DEGRADED


def test_readiness_flips_on_quarantine_and_breaker() -> None:
    runs = [_run("r1", state=RunState.COMPLETED, offset_seconds=10)]

    assert _service(runs, status="quarantined").workload("repo-agent").readiness is False
    assert _service(runs, breaker_open=True).workload("repo-agent").readiness is False


def test_mttr_is_computed_from_failure_and_recovery() -> None:
    runs = [
        _run("r1", state=RunState.FAILED, offset_seconds=300),
        _run("r2", state=RunState.COMPLETED, offset_seconds=200),
        _run("r3", state=RunState.FAILED, offset_seconds=150),
        _run("r4", state=RunState.COMPLETED, offset_seconds=50),
    ]
    service = _service(runs)

    health = service.workload("repo-agent")

    # recovery gaps: 100s (300->200) and 100s (150->50)
    assert health.mttr_seconds == 100


def test_mttr_is_none_without_a_failure() -> None:
    runs = [_run("r1", state=RunState.COMPLETED, offset_seconds=10)]

    assert _service(runs).workload("repo-agent").mttr_seconds is None


def test_window_excludes_old_runs() -> None:
    runs = [
        _run("r1", state=RunState.COMPLETED, offset_seconds=10),
        _run("r2", state=RunState.FAILED, offset_seconds=100_000),
    ]
    service = _service(runs)

    health = service.workload("repo-agent")

    assert health.terminal_runs == 1
    assert health.failed_runs == 0


def test_availability_error_budget_is_computed_from_failures() -> None:
    runs = [
        _run("r1", state=RunState.COMPLETED, offset_seconds=10),
        _run("r2", state=RunState.COMPLETED, offset_seconds=20),
        _run("r3", state=RunState.FAILED, offset_seconds=30),
    ]
    service = _service(runs)

    health = service.workload("repo-agent")
    availability = health.objective(Objective.AVAILABILITY)

    # target 0.9 over 3 events -> budget 0.3; 1 failure -> breached
    assert availability.error_budget == 0.3
    assert availability.consumed == 1.0
    assert availability.status is ObjectiveStatus.BREACHED


def test_quality_objective_uses_the_quality_score() -> None:
    runs = [_run("r1", state=RunState.COMPLETED, offset_seconds=10)]
    service = _service(runs, quality=0.5)

    health = service.workload("repo-agent")
    quality = health.objective(Objective.QUALITY)

    assert health.quality_score == 0.5
    assert quality.status is ObjectiveStatus.BREACHED


def test_insufficient_data_when_below_minimum() -> None:
    service = HealthService(
        run_history=lambda _w: [],
        quality_lookup=lambda _w: None,
        drift_lookup=lambda _w: "clean",
        breaker_lookup=lambda _w: False,
        status_lookup=lambda _w: "certified",
        slo_lookup=lambda _w: None,
        workload_lookup=lambda: ["repo-agent"],
        clock=lambda: _NOW,
        min_runs_for_score=5,
    )

    health = service.workload("repo-agent")

    assert health.insufficient_data is True
    assert health.status is HealthStatus.INSUFFICIENT_DATA


def test_burn_rate_uses_the_observed_vs_allowed_rate() -> None:
    runs = [
        _run("r1", state=RunState.COMPLETED, offset_seconds=10),
        _run("r2", state=RunState.FAILED, offset_seconds=20),
        _run("r3", state=RunState.FAILED, offset_seconds=30),
    ]
    service = _service(runs)

    burn = service.burn_rate("repo-agent", window_seconds=3600, critical_multiplier=14.4)

    # observed error rate 2/3; allowed 0.1 -> burn ~6.67
    assert burn.burn_rate > 1
    assert burn.alert is True


def test_burn_through_quarantines_on_availability_breach() -> None:
    runs = [
        _run("r1", state=RunState.COMPLETED, offset_seconds=10),
        _run("r2", state=RunState.FAILED, offset_seconds=20),
        _run("r3", state=RunState.FAILED, offset_seconds=30),
    ]
    service = _service(runs)

    action = service.burn_through("repo-agent")

    assert action is not None
    assert action.action == "quarantine"
    assert action.objective is Objective.AVAILABILITY


def test_burn_through_returns_none_when_healthy() -> None:
    runs = [_run("r1", state=RunState.COMPLETED, offset_seconds=10)]

    assert _service(runs).burn_through("repo-agent") is None


def test_fleet_health_lists_every_workload() -> None:
    runs = [_run("r1", state=RunState.COMPLETED, offset_seconds=10)]
    service = _service(runs)

    fleet = service.fleet()

    assert [health.workload for health in fleet] == ["repo-agent"]


def test_compute_health_is_pure_and_deterministic() -> None:
    runs = [_run("r1", state=RunState.COMPLETED, offset_seconds=10)]
    kwargs = {
        "workload": "repo-agent",
        "runs": runs,
        "now": _NOW,
        "window_seconds": 86400,
        "slo": SloTarget(availability_target=0.9, quality_target=0.8),
        "quality_score": 0.9,
        "drift_status": "clean",
        "breaker_open": False,
        "readiness": True,
        "min_runs_for_score": 1,
    }

    first = compute_health(**kwargs)  # type: ignore[arg-type]
    second = compute_health(**kwargs)  # type: ignore[arg-type]

    assert first == second
    assert BurnThroughAction is not None
