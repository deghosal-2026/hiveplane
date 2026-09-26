"""Synthetic probe scheduling, scoring, and early drift warning (M43-01..03)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from typing import Protocol

from hiveplane.probes.models import (
    ProbeOutcome,
    ProbeResult,
    ProbeSchedule,
    ProbeSpec,
    ProbeStatus,
    ProbeWarning,
)
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext


class ProbeRunner(Protocol):
    """Executes a probe on an isolated, non-delivering path."""

    def run(self, spec: ProbeSpec) -> ProbeOutcome: ...


class ProbeBudgetExceededError(Exception):
    """Raised when the separate probe budget cap is reached."""

    def __init__(self, workload_id: str) -> None:
        super().__init__(f"probe budget for {workload_id!r} is exhausted")
        self.workload_id = workload_id


class ProbeService:
    """Schedules and runs cheap ping-tasks on a separate, capped budget.

    Probes never deliver to fan-out and never count toward production SLOs or
    cost-per-completed-task (results are tagged ``probe``).
    """

    def __init__(
        self,
        *,
        runner: ProbeRunner | None = None,
        budget_cap_usd: float | None = None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._runner = runner
        self._budget_cap_usd = budget_cap_usd
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: f"probe-{uuid.uuid4().hex[:12]}")
        self._schedules: dict[str, ProbeSchedule] = {}
        self._results: dict[str, list[ProbeResult]] = {}

    def schedule(
        self, spec: ProbeSpec, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> ProbeSchedule:
        """Schedule a probe for a workload at its interval."""
        now = self._clock()
        schedule = ProbeSchedule(
            workload_id=spec.workload_id,
            interval_seconds=spec.interval_seconds,
            next_run=now,
            last_run=None,
            tenant_id=spec.tenant_id,
        )
        self._schedules[spec.workload_id] = schedule
        return schedule

    def schedules(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> list[ProbeSchedule]:
        """Return all probe schedules."""
        return sorted(self._schedules.values(), key=lambda item: item.workload_id)

    def due(self, *, at: datetime | None = None) -> list[ProbeSchedule]:
        """Return schedules whose next run has arrived."""
        now = at or self._clock()
        return [schedule for schedule in self.schedules() if schedule.next_run <= now]

    def probe_spend(self, workload_id: str) -> float:
        """Return the probe spend recorded for a workload (separate budget)."""
        return sum(result.cost_usd for result in self._results.get(workload_id, []))

    def run(self, spec: ProbeSpec, *, ctx: TenantContext = DEFAULT_CONTEXT) -> ProbeResult:
        """Run a probe, record pass/fail evidence, and advance the schedule."""
        spend = self.probe_spend(spec.workload_id)
        projected = self._last_cost(spec.workload_id)
        if self._budget_cap_usd is not None and spend + projected >= self._budget_cap_usd:
            raise ProbeBudgetExceededError(spec.workload_id)
        if self._runner is None:
            raise RuntimeError("no probe runner is configured")
        outcome = self._runner.run(spec)
        now = self._clock()
        passed = outcome.passed
        warning = (
            None
            if passed
            else ProbeWarning(
                workload_id=spec.workload_id,
                reason="synthetic probe failed (early drift warning)",
                created_at=now,
            )
        )
        result = ProbeResult(
            probe_id=self._id_factory(),
            workload_id=spec.workload_id,
            passed=passed,
            status=ProbeStatus.PASSED if passed else ProbeStatus.FAILED,
            latency_ms=outcome.latency_ms,
            cost_usd=outcome.cost_usd,
            detail=outcome.detail,
            warning=warning,
            created_at=now,
            tenant_id=spec.tenant_id,
        )
        self._results.setdefault(spec.workload_id, []).append(result)
        schedule = self._schedules.get(spec.workload_id)
        if schedule is not None:
            self._schedules[spec.workload_id] = schedule.model_copy(
                update={
                    "last_run": now,
                    "next_run": now + timedelta(seconds=schedule.interval_seconds),
                }
            )
        return result

    def list_results(
        self, workload_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[ProbeResult]:
        """Return recorded probe results for a workload."""
        return list(self._results.get(workload_id, []))

    def degraded(self, workload_id: str) -> bool:
        """Return True when the workload's most recent probe failed."""
        results = self._results.get(workload_id, [])
        return bool(results) and not results[-1].passed

    def clear(self) -> None:
        """Drop all schedules and results."""
        self._schedules.clear()
        self._results.clear()

    def _last_cost(self, workload_id: str) -> float:
        results = self._results.get(workload_id, [])
        return results[-1].cost_usd if results else 0.0
