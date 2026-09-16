"""Tests for budget enforcement through the run service."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane.budget.pricing import CostTable
from hiveplane.budget.service import BudgetService
from hiveplane.budget.store import InMemoryBudgetStore
from hiveplane.core.decision import DecisionOutcome
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.usage import UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.admission import AdmissionPipeline
from hiveplane.execution.fanout import FanOutService
from hiveplane.execution.models import DeliveryRecord
from hiveplane.execution.service import RunService
from hiveplane.execution.store import InMemoryRunStore
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore
from test_execution_admission import _Budget, _Cert, _Policy, _Sandbox
from test_execution_escalation import _Executor


class _FanOut:
    def notify(self, run: Run, workload: AgentWorkload) -> list[DeliveryRecord]:
        return []

    def notify_escalation(self, run: Run, workload: AgentWorkload) -> list[DeliveryRecord]:
        return []


def _clock() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def _service(
    make_manifest: Callable[..., AgentWorkload], *, per_run: float = 1.0
) -> tuple[RunService, str]:
    registry = RegistryService(InMemoryRegistryStore())
    workload = make_manifest(
        name="agent-1", budget={"per_run_usd": per_run, "per_day_usd": 100.0}
    )
    registry.create(workload)
    store = InMemoryRunStore()
    fanout = FanOutService(store, {}, clock=_clock)
    budget = BudgetService(InMemoryBudgetStore(), CostTable(), clock=_clock)
    admission = AdmissionPipeline(
        _Cert(True, "m1"),
        _Policy(DecisionOutcome.ALLOW),
        _Budget(True),
        _Sandbox(False),
        clock=_clock,
    )
    ids = iter(["run-1"])
    service = RunService(
        store,
        registry,
        admission=admission,
        executor=_Executor(),
        fanout=fanout,
        budget=budget,
        clock=_clock,
        id_factory=lambda: next(ids),
    )
    return service, "agent-1"


def _report(tokens: int) -> UsageReport:
    return UsageReport(
        run_id="run-1",
        input_tokens=tokens,
        output_tokens=0,
        tool_calls=1,
        cost_usd=0.0,
        timestamp=_clock(),
        model_identity="openai/gpt-4o/2024-08-06",
    )


def test_usage_within_budget_updates_cost(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, workload = _service(make_manifest)
    run = service.submit(workload=workload, caller="cli", context=AdmissionContext.SANDBOX)
    service.transition(run.id, RunState.RUNNING, actor="scheduler")
    updated = service.record_usage(run.id, _report(tokens=1000))
    assert updated.state is RunState.RUNNING
    assert updated.cost_usd > 0


def test_over_budget_usage_fails_the_run(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, workload = _service(make_manifest, per_run=0.001)
    run = service.submit(workload=workload, caller="cli", context=AdmissionContext.SANDBOX)
    service.transition(run.id, RunState.RUNNING, actor="scheduler")
    failed = service.record_usage(run.id, _report(tokens=1000))
    assert failed.state is RunState.FAILED
    assert failed.failure_reason is not None
