"""Per-tenant budgets and spend caps (M58-02)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from hiveplane.adapters.worker import RunControl, WorkerContext
from hiveplane.budget.pricing import CostTable
from hiveplane.budget.service import BudgetService
from hiveplane.budget.store import InMemoryBudgetStore
from hiveplane.core.decision import DecisionOutcome
from hiveplane.core.run import AdmissionContext, RunState
from hiveplane.core.usage import BudgetLevel, UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.cost.models import BudgetScope, CostEvent
from hiveplane.cost.service import CostService
from hiveplane.cost.store import InMemoryCostStore
from hiveplane.execution.admission import AdmissionPipeline
from hiveplane.execution.errors import RunAdmissionRefusedError
from hiveplane.execution.fanout import FanOutService
from hiveplane.execution.service import RunService
from hiveplane.execution.store import InMemoryRunStore
from hiveplane.fleet.cost import CostPeriodKind, CostType
from hiveplane.llm.fake import FakeProvider
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore
from hiveplane.tenancy.context import context_for_run
from test_execution_admission import _Budget as _FakeBudget
from test_execution_admission import _Cert, _Policy, _Sandbox
from test_execution_escalation import _Executor

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_ACME = context_for_run("acme")
_DEFAULT = context_for_run("default")
_OTHER = context_for_run("other")


def _clock() -> datetime:
    return _NOW


def _report(cost: float = 0.0, tokens: int = 0) -> UsageReport:
    return UsageReport(
        run_id="run-1",
        input_tokens=tokens,
        output_tokens=0,
        tool_calls=1,
        cost_usd=cost,
        timestamp=_NOW,
        model_identity="openai/gpt-4o/2024-08-06",
    )


def _cost_event(
    event_id: str,
    *,
    tenant_id: str = "acme",
    cost_usd: float = 0.01,
) -> CostEvent:
    return CostEvent(
        event_id=event_id,
        tenant_id=tenant_id,
        team_id="platform",
        workload_id="agent-1",
        cost_type=CostType.LLM,
        cost_usd=cost_usd,
        occurred_at=_NOW,
    )


def _bundle() -> tuple[BudgetService, InMemoryBudgetStore, CostService]:
    store = InMemoryBudgetStore()
    cost_service = CostService(InMemoryCostStore(), clock=_clock)
    service = BudgetService(store, CostTable(), clock=_clock, cost_service=cost_service)
    return service, store, cost_service


def _run_service(
    make_manifest: Callable[..., AgentWorkload], *, cap_usd: float | None = None
) -> tuple[RunService, InMemoryBudgetStore, CostService, str]:
    registry = RegistryService(InMemoryRegistryStore())
    workload = make_manifest(
        name="agent-1",
        budget={"per_run_usd": 100.0, "per_day_usd": 100.0, "per_team_usd": 100.0},
    )
    registry.create(workload, ctx=_ACME)
    run_store = InMemoryRunStore()
    fanout = FanOutService(run_store, {}, clock=_clock)
    budget_store = InMemoryBudgetStore()
    cost_service = CostService(InMemoryCostStore(), clock=_clock)
    budget = BudgetService(
        budget_store, CostTable(), clock=_clock, cost_service=cost_service
    )
    if cap_usd is not None:
        cost_service.set_budget(
            "acme",
            BudgetScope.TENANT,
            "acme",
            CostPeriodKind.DAY,
            limit_usd=100.0,
            cap_usd=cap_usd,
            enforced=True,
            ctx=_ACME,
        )
    admission = AdmissionPipeline(
        _Cert(True, "openai/gpt-4o/2024-08-06"),
        _Policy(DecisionOutcome.ALLOW),
        budget,
        _Sandbox(False),
        clock=_clock,
    )
    ids = iter(["run-1"])
    service = RunService(
        run_store,
        registry,
        admission=admission,
        executor=_Executor(),
        fanout=fanout,
        budget=budget,
        clock=_clock,
        id_factory=lambda: next(ids),
    )
    return service, budget_store, cost_service, "agent-1"


def test_record_usage_attributes_and_meters_to_tenant(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, budget_store, cost_service = _bundle()
    workload = make_manifest(name="agent-1")

    outcome = service.record_usage(workload, _report(tokens=1000), ctx=_ACME)

    assert outcome.cost_usd == pytest.approx(0.005)
    assert outcome.check.allowed is True
    assert budget_store.run_spend("run-1", ctx=_ACME) == pytest.approx(0.005)
    assert budget_store.day_spend("agent-1", "2026-01-01", ctx=_ACME) == pytest.approx(0.005)
    assert budget_store.team_spend("platform", "2026-01-01", ctx=_ACME) == pytest.approx(0.005)
    attributions = budget_store.list_attributions(ctx=_ACME)
    assert attributions and attributions[0].tenant_id == "acme"
    assert budget_store.run_spend("run-1", ctx=_DEFAULT) == 0.0
    assert budget_store.run_spend("run-1", ctx=_OTHER) == 0.0
    showback = cost_service.showback("acme", CostPeriodKind.DAY, ctx=_ACME)
    assert showback.total_cost_usd == pytest.approx(0.005)
    assert showback.rows
    assert cost_service.showback("default", CostPeriodKind.DAY, ctx=_DEFAULT).rows == []


def test_check_does_not_see_other_tenant_spend(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, store, _ = _bundle()
    workload = make_manifest(name="agent-1")
    store.add_day_spend("agent-1", "2026-01-01", 100.0, ctx=_OTHER)
    store.add_team_spend("platform", "2026-01-01", 100.0, ctx=_OTHER)

    foreign = service.check(workload, AdmissionContext.PRODUCTION, ctx=_ACME)
    assert foreign.allowed is True

    store.add_day_spend("agent-1", "2026-01-01", 5.0, ctx=_ACME)
    own = service.check(workload, AdmissionContext.PRODUCTION, ctx=_ACME)
    assert own.allowed is False
    assert own.level is BudgetLevel.DAY


def test_enforced_tenant_cap_blocks_admission(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, _, cost_service = _bundle()
    cost_service.set_budget(
        "acme",
        BudgetScope.TENANT,
        "acme",
        CostPeriodKind.DAY,
        limit_usd=100.0,
        cap_usd=0.001,
        enforced=True,
        ctx=_ACME,
    )
    cost_service.record(_cost_event("seed", cost_usd=0.01), ctx=_ACME)

    check = service.check(make_manifest(name="agent-1"), AdmissionContext.PRODUCTION, ctx=_ACME)

    assert check.allowed is False
    assert check.level is BudgetLevel.DAY
    assert check.reason == "tenant spend cap exceeded"


def test_run_usage_meters_to_run_tenant(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, budget_store, cost_service, workload = _run_service(make_manifest)
    run = service.submit(
        workload=workload, caller="cli", context=AdmissionContext.PRODUCTION, ctx=_ACME
    )
    service.transition(run.id, RunState.RUNNING, actor="scheduler", ctx=_ACME)

    updated = service.record_usage(run.id, _report(tokens=1000), ctx=_ACME)

    assert updated.state is RunState.RUNNING
    assert budget_store.run_spend("run-1", ctx=_ACME) == pytest.approx(0.005)
    assert budget_store.run_spend("run-1", ctx=_DEFAULT) == 0.0
    assert cost_service.showback("acme", CostPeriodKind.DAY, ctx=_ACME).rows
    assert cost_service.showback("default", CostPeriodKind.DAY, ctx=_DEFAULT).rows == []


def test_enforced_tenant_cap_refuses_submission(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, _, cost_service, workload = _run_service(make_manifest, cap_usd=0.001)
    cost_service.record(_cost_event("seed", cost_usd=0.01), ctx=_ACME)

    with pytest.raises(RunAdmissionRefusedError):
        service.submit(
            workload=workload, caller="cli", context=AdmissionContext.PRODUCTION, ctx=_ACME
        )


def test_usage_over_tenant_cap_fails_the_run(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, _, _, workload = _run_service(make_manifest, cap_usd=0.001)
    run = service.submit(
        workload=workload, caller="cli", context=AdmissionContext.PRODUCTION, ctx=_ACME
    )
    service.transition(run.id, RunState.RUNNING, actor="scheduler", ctx=_ACME)

    failed = service.record_usage(run.id, _report(tokens=1000), ctx=_ACME)

    assert failed.state is RunState.FAILED


def test_fake_budget_gate_accepts_ctx(make_manifest: Callable[..., AgentWorkload]) -> None:
    gate = _FakeBudget(True)
    check = gate.check(make_manifest(), AdmissionContext.PRODUCTION, ctx=_ACME)
    assert check.allowed is True


class _ToolsStub:
    def reset_drive(self, run_id: str) -> None:
        return None

    def invoke(self, run_id: str, request: object) -> object:
        raise AssertionError("tool calls are not used in this test")


def test_worker_reporter_meters_to_run_tenant(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, budget_store, cost_service, workload = _run_service(make_manifest)
    run = service.submit(
        workload=workload,
        caller="cli",
        context=AdmissionContext.SANDBOX,
        model_identity="openai/gpt-4o/2024-08-06",
        ctx=_ACME,
    )
    service.transition(run.id, RunState.RUNNING, actor="scheduler", ctx=_ACME)
    context = WorkerContext(
        run=run,
        workload=make_manifest(name="agent-1"),
        sandbox=True,
        tools=_ToolsStub(),  # type: ignore[arg-type]
        reporter=service,
        control=RunControl(),
        tool_calls=[],
        clock=_clock,
        provider=FakeProvider(),
        cost_table=CostTable(),
    )

    context.complete("hello")

    assert budget_store.run_spend(run.id, ctx=_ACME) > 0.0
    assert budget_store.run_spend(run.id, ctx=_DEFAULT) == 0.0
    assert cost_service.showback("acme", CostPeriodKind.DAY, ctx=_ACME).rows
    assert cost_service.showback("default", CostPeriodKind.DAY, ctx=_DEFAULT).rows == []
