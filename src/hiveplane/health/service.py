"""Agent health model, SLO/error-budget accounting, and burn throttle (M42).

Health is computed from real events: terminal runs (failure rate, MTTR), online
eval quality scores (M36), drift (M34), and circuit-breaker state (M41).
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from hiveplane.core.run import Run, RunState
from hiveplane.health.models import (
    BurnRate,
    BurnThroughAction,
    HealthStatus,
    Objective,
    ObjectiveStatus,
    SloObjective,
    SloTarget,
    WorkloadHealth,
)
from hiveplane.tenancy.context import DEFAULT_CONTEXT, TenantContext

_TERMINAL = (RunState.COMPLETED, RunState.FAILED)
#: A fast burn at/above this multiple pages (budget exhausts in ~2 days).
_CRITICAL_MULTIPLIER = 14.4


def _terminal_runs(runs: list[Run], now: datetime, window_seconds: int) -> list[Run]:
    cutoff = now - timedelta(seconds=window_seconds)
    selected = [
        run
        for run in runs
        if run.state in _TERMINAL and run.finished_at is not None and run.finished_at >= cutoff
    ]
    selected.sort(key=lambda run: (run.finished_at or run.updated_at, run.id))
    return selected


def _mttr_seconds(terminal: list[Run]) -> int | None:
    gaps: list[float] = []
    for index, run in enumerate(terminal):
        if run.state is not RunState.FAILED or run.finished_at is None:
            continue
        for later in terminal[index + 1 :]:
            if later.state is RunState.COMPLETED and later.finished_at is not None:
                gaps.append((later.finished_at - run.finished_at).total_seconds())
                break
    if not gaps:
        return None
    return int(sum(gaps) / len(gaps))


def _availability_objective(
    target: float, window_seconds: int, terminal: int, failed: int
) -> SloObjective:
    error_budget = (1.0 - target) * terminal
    consumed = float(failed)
    if consumed > error_budget:
        status = ObjectiveStatus.BREACHED
    elif error_budget > 0 and consumed > 0.5 * error_budget:
        status = ObjectiveStatus.AT_RISK
    else:
        status = ObjectiveStatus.OK
    return SloObjective(
        objective=Objective.AVAILABILITY,
        target=target,
        window_seconds=window_seconds,
        error_budget=round(error_budget, 10),
        consumed=consumed,
        remaining=round(max(0.0, error_budget - consumed), 10),
        observed=(1.0 - failed / terminal) if terminal else None,
        status=status,
    )


def _quality_objective(
    target: float, window_seconds: int, terminal: int, score: float
) -> SloObjective:
    error_budget = (1.0 - target) * terminal
    consumed = max(0.0, target - score) * terminal
    if score < target:
        status = ObjectiveStatus.BREACHED
    elif score < target + 0.05:
        status = ObjectiveStatus.AT_RISK
    else:
        status = ObjectiveStatus.OK
    return SloObjective(
        objective=Objective.QUALITY,
        target=target,
        window_seconds=window_seconds,
        error_budget=round(error_budget, 10),
        consumed=round(consumed, 10),
        remaining=round(max(0.0, error_budget - consumed), 10),
        observed=score,
        status=status,
    )


def compute_health(
    *,
    workload: str,
    runs: list[Run],
    now: datetime,
    window_seconds: int,
    slo: SloTarget | None,
    quality_score: float | None,
    drift_status: str,
    breaker_open: bool,
    readiness: bool,
    min_runs_for_score: int,
) -> WorkloadHealth:
    """Compute the health model for a workload from its events (pure)."""
    terminal = _terminal_runs(runs, now, window_seconds)
    failed = sum(1 for run in terminal if run.state is RunState.FAILED)
    failure_rate = failed / len(terminal) if terminal else 0.0
    objectives: list[SloObjective] = []
    if slo is not None and terminal:
        objectives.append(
            _availability_objective(
                slo.availability_target, slo.window_seconds, len(terminal), failed
            )
        )
        if slo.quality_target is not None and quality_score is not None:
            objectives.append(
                _quality_objective(
                    slo.quality_target, slo.window_seconds, len(terminal), quality_score
                )
            )
    insufficient_data = len(terminal) < min_runs_for_score
    if insufficient_data:
        status = HealthStatus.INSUFFICIENT_DATA
    elif not readiness:
        status = HealthStatus.UNHEALTHY
    elif any(entry.status is not ObjectiveStatus.OK for entry in objectives) or (
        drift_status != "clean"
    ):
        status = HealthStatus.DEGRADED
    else:
        status = HealthStatus.HEALTHY
    return WorkloadHealth(
        workload=workload,
        window_seconds=window_seconds,
        terminal_runs=len(terminal),
        failed_runs=failed,
        failure_rate=failure_rate,
        mttr_seconds=_mttr_seconds(terminal),
        readiness=readiness,
        quality_score=quality_score,
        drift_status=drift_status,
        breaker_open=breaker_open,
        objectives=objectives,
        insufficient_data=insufficient_data,
        status=status,
        generated_at=now,
    )


class HealthService:
    """Computes per-workload and fleet health from injected event lookups."""

    def __init__(
        self,
        *,
        run_history: Callable[..., list[Run]],
        quality_lookup: Callable[..., float | None],
        drift_lookup: Callable[..., str],
        breaker_lookup: Callable[..., bool],
        status_lookup: Callable[..., str],
        slo_lookup: Callable[..., SloTarget | None],
        workload_lookup: Callable[..., list[str]],
        clock: Callable[[], datetime] | None = None,
        window_seconds: int = 86400,
        min_runs_for_score: int = 5,
        quarantine: Callable[..., None] | None = None,
        throttle: Callable[..., None] | None = None,
        alert_debounce_seconds: int = 300,
    ) -> None:
        self._run_history = run_history
        self._quality_lookup = quality_lookup
        self._drift_lookup = drift_lookup
        self._breaker_lookup = breaker_lookup
        self._status_lookup = status_lookup
        self._slo_lookup = slo_lookup
        self._workload_lookup = workload_lookup
        self._clock = clock or (lambda: datetime.now(UTC))
        self._window = window_seconds
        self._min_runs = min_runs_for_score
        self._quarantine = quarantine
        self._throttle = throttle
        self._alert_debounce = alert_debounce_seconds
        self._alert_state: dict[tuple[str, str], datetime] = {}

    def workload(
        self, name: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> WorkloadHealth:
        """Return the health model for one workload."""
        status = self._status_lookup(name, ctx=ctx)
        breaker_open = self._breaker_lookup(name, ctx=ctx)
        readiness = status not in {"quarantined", "uncertified"} and not breaker_open
        now = self._clock()
        return compute_health(
            workload=name,
            runs=self._run_history(
                name,
                ctx=ctx,
                finished_after=now - timedelta(seconds=self._window),
                states=_TERMINAL,
            ),
            now=now,
            window_seconds=self._window,
            slo=self._slo_lookup(name, ctx=ctx),
            quality_score=self._quality_lookup(name, ctx=ctx),
            drift_status=self._drift_lookup(name, ctx=ctx),
            breaker_open=breaker_open,
            readiness=readiness,
            min_runs_for_score=self._min_runs,
        )

    def fleet(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> list[WorkloadHealth]:
        """Return the health model for every workload in the acting tenant."""
        return [self.workload(name, ctx=ctx) for name in sorted(self._workload_lookup(ctx=ctx))]

    def burn_rate(
        self,
        name: str,
        *,
        window_seconds: int = 3600,
        critical_multiplier: float = _CRITICAL_MULTIPLIER,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> BurnRate:
        """Return the burn rate for a workload's availability objective."""
        slo = self._slo_lookup(name, ctx=ctx)
        target = slo.availability_target if slo is not None else 0.99
        now = self._clock()
        terminal = _terminal_runs(
            self._run_history(
                name,
                ctx=ctx,
                finished_after=now - timedelta(seconds=window_seconds),
                states=_TERMINAL,
            ),
            now,
            window_seconds,
        )
        failed = sum(1 for run in terminal if run.state is RunState.FAILED)
        observed = failed / len(terminal) if terminal else 0.0
        allowed = 1.0 - target
        burn = (observed / allowed) if allowed > 0 else 0.0
        return BurnRate(
            workload=name,
            window_seconds=window_seconds,
            observed_error_rate=observed,
            allowed_error_rate=allowed,
            burn_rate=burn,
            critical=burn >= critical_multiplier,
            alert=burn >= 1.0,
        )

    def burn_through(
        self, name: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> BurnThroughAction | None:
        """Return the automatic action for an exhausted/severely-burning objective."""
        health = self.workload(name, ctx=ctx)
        availability = next(
            (entry for entry in health.objectives if entry.objective is Objective.AVAILABILITY),
            None,
        )
        if availability is not None and availability.status is ObjectiveStatus.BREACHED:
            return BurnThroughAction(
                workload=name,
                action="quarantine",
                objective=Objective.AVAILABILITY,
                reason=(
                    f"availability error budget exhausted "
                    f"({availability.consumed}/{availability.error_budget})"
                ),
            )
        fast = self.burn_rate(name, ctx=ctx)
        if fast.critical:
            return BurnThroughAction(
                workload=name,
                action="throttle",
                objective=Objective.AVAILABILITY,
                reason=f"fast burn {fast.burn_rate:.1f}x exceeds critical threshold",
                burn_rate=fast.burn_rate,
            )
        return None

    def enforce(
        self, name: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> BurnThroughAction | None:
        """Apply the burn-through action (quarantine or throttle) for a workload."""
        action = self.burn_through(name, ctx=ctx)
        if action is not None:
            self._apply(action, ctx=ctx)
        return action

    def sweep(
        self, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[BurnThroughAction]:
        """Evaluate every workload and apply burn-through actions automatically.

        This is the scheduled path: an exhausted error budget quarantines and a
        critical fast burn throttles without an operator calling ``enforce``. A
        debounce window keeps a sustained breach from re-firing the same action
        every tick.
        """
        actions: list[BurnThroughAction] = []
        for name in sorted(self._workload_lookup(ctx=ctx)):
            action = self.burn_through(name, ctx=ctx)
            if action is None or not self._should_fire(name, action.action):
                continue
            self._apply(action, ctx=ctx)
            actions.append(action)
        return actions

    def _apply(self, action: BurnThroughAction, *, ctx: TenantContext) -> None:
        if action.action == "quarantine" and self._quarantine is not None:
            self._quarantine(action.workload, action.reason, ctx=ctx)
        elif action.action == "throttle" and self._throttle is not None:
            self._throttle(action.workload, action.reason, ctx=ctx)

    def _should_fire(self, workload: str, action: str) -> bool:
        now = self._clock()
        key = (workload, action)
        last = self._alert_state.get(key)
        if last is not None and (now - last).total_seconds() < self._alert_debounce:
            return False
        self._alert_state[key] = now
        return True
