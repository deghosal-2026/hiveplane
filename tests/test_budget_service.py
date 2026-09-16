"""Tests for the budget service."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from hiveplane.budget.errors import UnknownModelPriceError
from hiveplane.budget.pricing import CostTable
from hiveplane.budget.service import BudgetService
from hiveplane.budget.store import InMemoryBudgetStore
from hiveplane.core.run import AdmissionContext
from hiveplane.core.usage import BudgetLevel, UsageReport
from hiveplane.core.workload import AgentWorkload


def _clock() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


class _Metrics:
    def __init__(self) -> None:
        self.spends: list[float] = []
        self.exceeded: list[BudgetLevel] = []

    def record_spend(self, cost_usd: float, workload: str, team: str | None) -> None:
        self.spends.append(cost_usd)

    def record_exceeded(self, level: BudgetLevel, workload: str) -> None:
        self.exceeded.append(level)


def _service() -> tuple[BudgetService, InMemoryBudgetStore, _Metrics]:
    store = InMemoryBudgetStore()
    metrics = _Metrics()
    service = BudgetService(store, CostTable(), metrics=metrics, clock=_clock)
    return service, store, metrics


def _report(cost: float = 0.0, tokens: int = 0) -> UsageReport:
    return UsageReport(
        run_id="run-1",
        input_tokens=tokens,
        output_tokens=0,
        tool_calls=1,
        cost_usd=cost,
        timestamp=_clock(),
        model_identity="openai/gpt-4o/2024-08-06",
    )


def test_check_allows_within_budget(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, _, _ = _service()
    check = service.check(make_manifest(), AdmissionContext.PRODUCTION)
    assert check.allowed is True
    assert check.level is BudgetLevel.RUN


def test_check_denies_when_day_exhausted(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, store, _ = _service()
    workload = make_manifest()
    store.add_day_spend(workload.name, "2026-01-01", workload.spec.budget.per_day_usd)
    check = service.check(workload, AdmissionContext.PRODUCTION)
    assert check.allowed is False
    assert check.level is BudgetLevel.DAY


def test_record_usage_prices_tokens_and_attributes(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, store, metrics = _service()
    workload = make_manifest()
    outcome = service.record_usage(workload, _report(tokens=1000))
    assert outcome.cost_usd == pytest.approx(0.005)
    assert outcome.check.allowed is True
    assert store.run_spend("run-1") == pytest.approx(0.005)
    assert store.list_attributions(workload="agent-1")[0].tool_calls == 1
    assert metrics.spends == [pytest.approx(0.005)]


def test_record_usage_blocks_run_over_per_run_budget(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, _, metrics = _service()
    workload = make_manifest(budget={"per_run_usd": 0.001, "per_day_usd": 5.0})
    outcome = service.record_usage(workload, _report(tokens=1000))
    assert outcome.check.allowed is False
    assert outcome.check.level is BudgetLevel.RUN
    assert BudgetLevel.RUN in metrics.exceeded


def test_record_usage_blocks_run_over_day_budget(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, store, _ = _service()
    workload = make_manifest(budget={"per_run_usd": 1.0, "per_day_usd": 1.0})
    store.add_day_spend(workload.name, "2026-01-01", 0.999)
    outcome = service.record_usage(workload, _report(tokens=1000))
    assert outcome.check.allowed is False
    assert outcome.check.level is BudgetLevel.DAY


def test_unknown_model_fails_loudly(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, _, _ = _service()
    report = UsageReport(
        run_id="run-1",
        input_tokens=1,
        output_tokens=1,
        tool_calls=0,
        cost_usd=0.0,
        timestamp=_clock(),
        model_identity="acme/mystery/1",
    )
    with pytest.raises(UnknownModelPriceError):
        service.record_usage(make_manifest(), report)


def test_snapshot_reports_spend(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, _, _ = _service()
    workload = make_manifest()
    service.record_usage(workload, _report(tokens=1000))
    snapshot = service.snapshot(workload, "run-1")
    assert snapshot.run_usd == pytest.approx(0.005)
    assert snapshot.team_usd == pytest.approx(0.005)
    assert snapshot.day == "2026-01-01"


def test_check_denies_when_team_exhausted(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, store, _ = _service()
    workload = make_manifest(team="platform")
    store.add_team_spend("platform", "2026-01-01", workload.spec.budget.per_team_usd or 50.0)
    check = service.check(workload, AdmissionContext.PRODUCTION)
    assert check.allowed is False
    assert check.level is BudgetLevel.TEAM


def test_record_usage_without_model_identity_uses_reported_cost(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, _, _ = _service()
    report = UsageReport(
        run_id="run-1",
        input_tokens=0,
        output_tokens=0,
        tool_calls=0,
        cost_usd=0.02,
        timestamp=_clock(),
    )
    outcome = service.record_usage(make_manifest(), report)
    assert outcome.cost_usd == 0.02
