"""Tests for the agent health model, SLO/error budget, and burn throttle (M42)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.execution.store import InMemoryRunStore
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
    quarantine: Callable[..., None] | None = None,
    throttle: Callable[..., None] | None = None,
    availability_target: float = 0.9,
) -> HealthService:
    return HealthService(
        run_history=lambda _workload, *, ctx, finished_after=None, states=None: runs,
        quality_lookup=lambda _workload, *, ctx: quality,
        drift_lookup=lambda _workload, *, ctx: drift,
        breaker_lookup=lambda _workload, *, ctx: breaker_open,
        status_lookup=lambda _workload, *, ctx: status,
        slo_lookup=lambda _workload, *, ctx: SloTarget(
            availability_target=availability_target,
            quality_target=0.8,
            window_seconds=86400,
        ),
        workload_lookup=lambda *, ctx: ["repo-agent"],
        clock=lambda: _NOW,
        min_runs_for_score=1,
        quarantine=quarantine,
        throttle=throttle,
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
        run_history=lambda _w, *, ctx, finished_after=None, states=None: [],
        quality_lookup=lambda _w, *, ctx: None,
        drift_lookup=lambda _w, *, ctx: "clean",
        breaker_lookup=lambda _w, *, ctx: False,
        status_lookup=lambda _w, *, ctx: "certified",
        slo_lookup=lambda _w, *, ctx: None,
        workload_lookup=lambda *, ctx: ["repo-agent"],
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


def test_burn_through_auto_quarantines_without_manual_call() -> None:
    runs = [
        _run("r1", state=RunState.COMPLETED, offset_seconds=10),
        _run("r2", state=RunState.FAILED, offset_seconds=20),
        _run("r3", state=RunState.FAILED, offset_seconds=30),
    ]
    quarantined: list[str] = []
    service = _service(
        runs, quarantine=lambda name, reason, *, ctx: quarantined.append(name)
    )

    actions = service.sweep()

    assert [action.action for action in actions] == ["quarantine"]
    assert quarantined == ["repo-agent"]
    # the debounce stops the same breach re-firing on the next tick
    assert service.sweep() == []


def test_fast_burn_critical_applies_throttle() -> None:
    # one failure inside the fast-burn window; plenty of history in the SLO window
    runs = [_run("burst", state=RunState.FAILED, offset_seconds=10)] + [
        _run(f"ok-{index}", state=RunState.COMPLETED, offset_seconds=4000 + index)
        for index in range(99)
    ]
    throttled: list[str] = []
    service = _service(
        runs,
        availability_target=0.99,
        throttle=lambda name, reason, *, ctx: throttled.append(name),
    )

    actions = service.sweep()

    assert [action.action for action in actions] == ["throttle"]
    assert throttled == ["repo-agent"]


def test_availability_slo_target_one_oh_breaches_and_quarantines() -> None:
    runs = [
        *[
            _run(f"c{i}", state=RunState.COMPLETED, offset_seconds=10 + i)
            for i in range(5)
        ],
        *[
            _run(f"f{i}", state=RunState.FAILED, offset_seconds=20 + i)
            for i in range(5)
        ],
    ]
    quarantined: list[str] = []
    service = HealthService(
        run_history=lambda _w, *, ctx, finished_after=None, states=None: runs,
        quality_lookup=lambda _w, *, ctx: None,
        drift_lookup=lambda _w, *, ctx: "clean",
        breaker_lookup=lambda _w, *, ctx: False,
        status_lookup=lambda _w, *, ctx: "certified",
        slo_lookup=lambda _w, *, ctx: SloTarget(
            availability_target=1.0, window_seconds=86400
        ),
        workload_lookup=lambda *, ctx: ["repo-agent"],
        clock=lambda: _NOW,
        min_runs_for_score=1,
        quarantine=lambda name, reason, *, ctx: quarantined.append(name),
    )

    availability = service.workload("repo-agent").objective(Objective.AVAILABILITY)

    # target 1.0 -> zero error budget; any failure exceeds it
    assert availability.error_budget == 0.0
    assert availability.consumed == 5.0
    assert availability.status is ObjectiveStatus.BREACHED

    action = service.burn_through("repo-agent")
    assert action is not None
    assert action.action == "quarantine"
    assert service.enforce("repo-agent") is not None
    assert quarantined == ["repo-agent"]


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


class _CountingRunStore(InMemoryRunStore):
    """A run store that records how many runs the health path materialized."""

    def __init__(self) -> None:
        super().__init__()
        self.fetched = 0
        self.last_states: tuple[RunState, ...] | None = None
        self.last_finished_after: datetime | None = None

    def list_runs(
        self,
        *,
        workload: str | None = None,
        state: RunState | None = None,
        finished_after: datetime | None = None,
        states: tuple[RunState, ...] | None = None,
        ctx: object = None,
    ) -> list[Run]:
        result = super().list_runs(
            workload=workload,
            state=state,
            finished_after=finished_after,
            states=states,
            ctx=ctx,  # type: ignore[arg-type]
        )
        self.fetched += len(result)
        self.last_states = states
        self.last_finished_after = finished_after
        return result


def test_fleet_health_ignores_runs_outside_window_at_store_level() -> None:
    store = _CountingRunStore()
    store.save_run(_run("recent-ok", state=RunState.COMPLETED, offset_seconds=10))
    store.save_run(_run("recent-bad", state=RunState.FAILED, offset_seconds=20))
    store.save_run(_run("old-ok", state=RunState.COMPLETED, offset_seconds=100_000))
    store.save_run(_run("recent-running", state=RunState.RUNNING, offset_seconds=5))

    service = HealthService(
        run_history=lambda workload, *, ctx, finished_after=None, states=None: (
            store.list_runs(
                workload=workload,
                ctx=ctx,
                finished_after=finished_after,
                states=states,
            )
        ),
        quality_lookup=lambda _w, *, ctx: None,
        drift_lookup=lambda _w, *, ctx: "clean",
        breaker_lookup=lambda _w, *, ctx: False,
        status_lookup=lambda _w, *, ctx: "certified",
        slo_lookup=lambda _w, *, ctx: SloTarget(
            availability_target=0.9, window_seconds=86400
        ),
        workload_lookup=lambda *, ctx: ["repo-agent"],
        clock=lambda: _NOW,
        min_runs_for_score=1,
    )

    fleet = service.fleet()

    assert [health.workload for health in fleet] == ["repo-agent"]
    # only the two in-window terminal runs are ever fetched from the store;
    # the out-of-window and non-terminal runs are excluded by the query.
    assert store.fetched == 2
    assert store.last_states == (RunState.COMPLETED, RunState.FAILED)
    assert store.last_finished_after == _NOW - timedelta(seconds=86400)
