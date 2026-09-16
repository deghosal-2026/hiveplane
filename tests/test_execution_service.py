"""Tests for the run service."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from hiveplane.core.decision import DecisionOutcome
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.usage import UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.admission import AdmissionPipeline
from hiveplane.execution.errors import (
    IllegalTransitionError,
    RunAdmissionRefusedError,
    RunNotFoundError,
    RunNotIntervenableError,
)
from hiveplane.execution.models import DeliveryRecord, InterventionAction, RunContext
from hiveplane.execution.service import RunService
from hiveplane.execution.store import InMemoryRunStore
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore
from test_execution_admission import _Budget, _Cert, _Policy, _Sandbox


def _clock() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


class _FanOut:
    def __init__(self) -> None:
        self.notified: list[str] = []

    def notify(self, run: Run, workload: AgentWorkload) -> list[DeliveryRecord]:
        self.notified.append(run.id)
        return []


class _Executor:
    def __init__(self) -> None:
        self.started: list[str] = []
        self.paused: list[str] = []
        self.cancelled: list[str] = []

    def start(self, context: RunContext) -> None:
        self.started.append(context.run.id)

    def pause(self, run_id: str) -> bool:
        self.paused.append(run_id)
        return True

    def resume(self, run_id: str) -> bool:
        return True

    def cancel(self, run_id: str) -> None:
        self.cancelled.append(run_id)

    def status(self, run_id: str) -> RunState:
        return RunState.RUNNING

    def usage(self, run_id: str) -> UsageReport | None:
        return None


def _service(
    make_manifest: Callable[..., AgentWorkload],
    *,
    admitted: bool = True,
    policy_outcome: DecisionOutcome = DecisionOutcome.ALLOW,
) -> tuple[RunService, _Executor, _FanOut, str]:
    registry = RegistryService(InMemoryRegistryStore())
    workload = make_manifest(name="agent-1")
    registry.create(workload)
    executor = _Executor()
    fanout = _FanOut()
    admission = AdmissionPipeline(
        _Cert(admitted, "m1"),
        _Policy(policy_outcome),
        _Budget(True),
        _Sandbox(False),
        clock=_clock,
    )
    ids = iter(["run-1", "run-2", "run-3"])
    service = RunService(
        InMemoryRunStore(),
        registry,
        admission=admission,
        executor=executor,
        fanout=fanout,
        clock=_clock,
        id_factory=lambda: next(ids),
    )
    return service, executor, fanout, "agent-1"


def test_submit_persists_run_and_admission(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, _, _, workload = _service(make_manifest)
    run = service.submit(
        workload=workload,
        task={"x": 1},
        caller="cli",
        context=AdmissionContext.PRODUCTION,
        model_identity="m1",
    )
    assert run.id == "run-1"
    assert run.state is RunState.QUEUED
    assert service.events("run-1")[0].type.value == "admission"


def test_submit_refused_raises_and_persists_nothing(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, _, _, workload = _service(make_manifest, admitted=False)
    with pytest.raises(RunAdmissionRefusedError):
        service.submit(workload=workload, caller="cli", context=AdmissionContext.PRODUCTION)
    with pytest.raises(RunNotFoundError):
        service.get("run-1")


def test_submit_escalation_creates_paused_run(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, _, _, workload = _service(make_manifest, policy_outcome=DecisionOutcome.ESCALATE)
    run = service.submit(
        workload=workload,
        caller="cli",
        context=AdmissionContext.PRODUCTION,
        model_identity="m1",
    )
    assert run.state is RunState.PAUSED


def test_transition_records_event_and_starts_executor(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, executor, _, workload = _service(make_manifest)
    run = service.submit(
        workload=workload,
        caller="cli",
        context=AdmissionContext.PRODUCTION,
        model_identity="m1",
    )
    running = service.transition(run.id, RunState.RUNNING, actor="scheduler")
    assert running.state is RunState.RUNNING
    assert executor.started == ["run-1"]
    assert service.events(run.id)[-1].to_state is RunState.RUNNING


def test_illegal_transition_raises(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, _, _, workload = _service(make_manifest)
    run = service.submit(
        workload=workload,
        caller="cli",
        context=AdmissionContext.PRODUCTION,
        model_identity="m1",
    )
    service.transition(run.id, RunState.RUNNING, actor="scheduler")
    service.transition(run.id, RunState.COMPLETED, actor="runtime")
    with pytest.raises(IllegalTransitionError):
        service.transition(run.id, RunState.RUNNING, actor="runtime")


def test_terminal_transition_triggers_fanout(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, _, fanout, workload = _service(make_manifest)
    run = service.submit(
        workload=workload,
        caller="cli",
        context=AdmissionContext.PRODUCTION,
        model_identity="m1",
    )
    service.transition(run.id, RunState.RUNNING, actor="scheduler")
    service.transition(run.id, RunState.COMPLETED, actor="runtime")
    assert fanout.notified == ["run-1"]


def test_intervene_pause_and_stop(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, executor, _, workload = _service(make_manifest)
    run = service.submit(
        workload=workload,
        caller="cli",
        context=AdmissionContext.PRODUCTION,
        model_identity="m1",
    )
    service.transition(run.id, RunState.RUNNING, actor="scheduler")
    paused = service.intervene(run.id, InterventionAction.PAUSE, actor="operator")
    assert paused.state is RunState.PAUSED
    assert executor.paused == ["run-1"]
    stopped = service.intervene(run.id, InterventionAction.STOP, actor="operator")
    assert stopped.state is RunState.CANCELLED
    assert executor.cancelled == ["run-1"]


def test_intervene_invalid_state_raises(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, _, _, workload = _service(make_manifest)
    run = service.submit(
        workload=workload,
        caller="cli",
        context=AdmissionContext.PRODUCTION,
        model_identity="m1",
    )
    with pytest.raises(RunNotIntervenableError):
        service.intervene(run.id, InterventionAction.PAUSE, actor="operator")


def test_record_usage_updates_cost(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, _, _, workload = _service(make_manifest)
    run = service.submit(
        workload=workload,
        caller="cli",
        context=AdmissionContext.PRODUCTION,
        model_identity="m1",
    )
    updated = service.record_usage(
        run.id,
        UsageReport(
            run_id=run.id,
            input_tokens=1,
            output_tokens=2,
            tool_calls=1,
            cost_usd=0.25,
            timestamp=_clock(),
        ),
    )
    assert updated.cost_usd == 0.25
    assert len(service.usage(run.id)) == 1
