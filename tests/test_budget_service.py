"""Tests for the budget service."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from hiveplane.budget.errors import MissingModelIdentityError, UnknownModelPriceError
from hiveplane.budget.pricing import CostTable
from hiveplane.budget.service import BudgetService
from hiveplane.budget.store import InMemoryBudgetStore
from hiveplane.core.run import AdmissionContext
from hiveplane.core.usage import BudgetLevel, UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.cost.models import BudgetScope, CostEvent
from hiveplane.cost.service import CostService
from hiveplane.cost.store import InMemoryCostStore
from hiveplane.fleet.cost import CostPeriodKind
from hiveplane.tenancy import DEFAULT_CONTEXT, Role, TenantContext
from hiveplane.tenancy.context import DEFAULT_TENANT_ID, context_for_run
from metrics import RecordingMetrics


def _clock() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def _service() -> tuple[BudgetService, InMemoryBudgetStore]:
    store = InMemoryBudgetStore()
    service = BudgetService(store, CostTable(), clock=_clock)
    return service, store


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
    service, _ = _service()
    check = service.check(make_manifest(), AdmissionContext.PRODUCTION)
    assert check.allowed is True
    assert check.level is BudgetLevel.RUN


def test_check_denies_when_day_exhausted(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, store = _service()
    workload = make_manifest()
    store.add_day_spend(workload.name, "2026-01-01", workload.spec.budget.per_day_usd)
    check = service.check(workload, AdmissionContext.PRODUCTION)
    assert check.allowed is False
    assert check.level is BudgetLevel.DAY


def test_record_usage_prices_tokens_and_attributes(
    make_manifest: Callable[..., AgentWorkload], fleet_metrics: RecordingMetrics
) -> None:
    service, store = _service()
    workload = make_manifest()
    outcome = service.record_usage(workload, _report(tokens=1000))
    assert outcome.cost_usd == pytest.approx(0.005)
    assert outcome.check.allowed is True
    assert store.run_spend("run-1") == pytest.approx(0.005)
    assert store.list_attributions(workload="agent-1")[0].tool_calls == 1
    spends = fleet_metrics.called("record_spend")
    assert spends == [
        {
            "workload": "agent-1",
            "team": "platform",
            "model": "openai/gpt-4o/2024-08-06",
            "cost_usd": pytest.approx(0.005),
        }
    ]


def test_record_usage_blocks_run_over_per_run_budget(
    make_manifest: Callable[..., AgentWorkload], fleet_metrics: RecordingMetrics
) -> None:
    service, _ = _service()
    workload = make_manifest(budget={"per_run_usd": 0.001, "per_day_usd": 5.0})
    outcome = service.record_usage(workload, _report(tokens=1000))
    assert outcome.check.allowed is False
    assert outcome.check.level is BudgetLevel.RUN
    exceeded = fleet_metrics.called("record_budget_exceeded")
    assert exceeded == [{"workload": "agent-1", "team": "platform", "level": "run"}]


def test_record_usage_blocks_run_over_day_budget(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, store = _service()
    workload = make_manifest(budget={"per_run_usd": 1.0, "per_day_usd": 1.0})
    store.add_day_spend(workload.name, "2026-01-01", 0.999)
    outcome = service.record_usage(workload, _report(tokens=1000))
    assert outcome.check.allowed is False
    assert outcome.check.level is BudgetLevel.DAY


def test_unknown_model_fails_loudly(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, _ = _service()
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
    service, _ = _service()
    workload = make_manifest()
    service.record_usage(workload, _report(tokens=1000))
    snapshot = service.snapshot(workload, "run-1")
    assert snapshot.run_usd == pytest.approx(0.005)
    assert snapshot.team_usd == pytest.approx(0.005)
    assert snapshot.day == "2026-01-01"


def test_check_denies_when_team_exhausted(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, store = _service()
    workload = make_manifest(team="platform")
    store.add_team_spend("platform", "2026-01-01", workload.spec.budget.per_team_usd or 50.0)
    check = service.check(workload, AdmissionContext.PRODUCTION)
    assert check.allowed is False
    assert check.level is BudgetLevel.TEAM


def test_check_does_not_see_another_tenants_spend(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, store = _service()
    workload = make_manifest()
    store.add_day_spend(
        workload.name,
        "2026-01-01",
        workload.spec.budget.per_day_usd,
        ctx=context_for_run("acme"),
    )
    check = service.check(workload, AdmissionContext.PRODUCTION, ctx=DEFAULT_CONTEXT)
    assert check.allowed is True


def test_record_usage_without_model_identity_is_rejected(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, _ = _service()
    report = UsageReport(
        run_id="run-1",
        input_tokens=0,
        output_tokens=0,
        tool_calls=0,
        cost_usd=0.02,
        timestamp=_clock(),
    )

    with pytest.raises(MissingModelIdentityError):
        service.record_usage(make_manifest(), report)


def test_monthly_tenant_cap_blocks_admission(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    """An enforced MONTH cap must deny admission even with no DAY cap."""
    ctx = TenantContext(tenant_id=DEFAULT_TENANT_ID, role=Role.ADMIN)
    cost = CostService(InMemoryCostStore(), clock=_clock)
    cost.set_budget(
        DEFAULT_TENANT_ID,
        BudgetScope.TENANT,
        DEFAULT_TENANT_ID,
        CostPeriodKind.MONTH,
        limit_usd=100.0,
        cap_usd=50.0,
        enforced=True,
        ctx=ctx,
    )
    cost.record(
        CostEvent(
            event_id="e1",
            tenant_id=DEFAULT_TENANT_ID,
            team_id="platform",
            workload_id="agent-1",
            cost_usd=60.0,
            occurred_at=_clock(),
        ),
        ctx=ctx,
    )

    store = InMemoryBudgetStore()
    service = BudgetService(store, CostTable(), clock=_clock, cost_service=cost)
    check = service.check(make_manifest(), AdmissionContext.PRODUCTION, ctx=ctx)
    assert check.allowed is False
    assert check.reason == "tenant spend cap exceeded"
