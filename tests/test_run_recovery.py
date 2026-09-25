"""Tests for startup recovery and durable resume (M23, #111).

On boot the control plane reconciles runs left non-terminal by a previous
process: paused runs are re-attached to their adapter so an operator can resume
them, and running runs whose worker died are reconciled to a defined terminal
state instead of sitting in ``running`` forever.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
from hiveplane.core.decision import DecisionOutcome
from hiveplane.core.event import EventType
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.usage import UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.admission import AdmissionPipeline
from hiveplane.execution.models import DeliveryRecord, InterventionAction, RunContext
from hiveplane.execution.recovery import RecoveryReport, RunRecovery
from hiveplane.execution.service import RunService
from hiveplane.execution.store import InMemoryRunStore, JsonFileRunStore
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore
from test_execution_admission import _Budget, _Cert, _Policy, _Sandbox

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _clock() -> datetime:
    return _NOW


class _FanOut:
    def __init__(self) -> None:
        self.notified: list[str] = []

    def notify(self, run: Run, workload: AgentWorkload) -> list[DeliveryRecord]:
        self.notified.append(run.id)
        return []

    def notify_escalation(self, run: Run, workload: AgentWorkload) -> list[DeliveryRecord]:
        self.notified.append(run.id)
        return []


class _Executor:
    """Executor double that records re-attach and resume calls."""

    def __init__(self, *, reattachable: bool = True) -> None:
        self.reattached: list[str] = []
        self.resumed: list[str] = []
        self._reattachable = reattachable

    def start(self, context: RunContext) -> None:
        return None

    def pause(self, run_id: str) -> bool:
        return True

    def resume(self, run_id: str) -> bool:
        self.resumed.append(run_id)
        return True

    def cancel(self, run_id: str) -> None:
        return None

    def status(self, run_id: str) -> RunState:
        return RunState.RUNNING

    def usage(self, run_id: str) -> UsageReport | None:
        return None

    def reattach(self, context: RunContext) -> bool:
        if not self._reattachable:
            return False
        self.reattached.append(context.run.id)
        return True


def _service(
    make_manifest: Callable[..., AgentWorkload],
    store: InMemoryRunStore | JsonFileRunStore,
    executor: _Executor,
) -> RunService:
    registry = RegistryService(InMemoryRegistryStore())
    registry.create(make_manifest(name="agent-1"))
    return RunService(
        store,
        registry,
        admission=AdmissionPipeline(
            _Cert(True, "m1"),
            _Policy(DecisionOutcome.ALLOW),
            _Budget(True),
            _Sandbox(False),
            clock=_clock,
        ),
        executor=executor,
        fanout=_FanOut(),
        clock=_clock,
    )


def _seed(store: InMemoryRunStore | JsonFileRunStore, run_id: str, state: RunState) -> Run:
    run = Run(
        id=run_id,
        workload_id="agent-1",
        caller="cli",
        state=state,
        context=AdmissionContext.SANDBOX,
        created_at=_NOW,
        updated_at=_NOW,
    )
    store.save_run(run)
    return run


def test_recovery_fails_running_runs_whose_worker_died(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    store = InMemoryRunStore()
    service = _service(make_manifest, store, _Executor())
    _seed(store, "run-running", RunState.RUNNING)

    report = RunRecovery(service).run()

    assert report.failed == ["run-running"]
    run = service.get("run-running")
    assert run.state is RunState.FAILED
    assert run.failure_reason == "interrupted by control-plane restart"
    events = [event for event in service.events("run-running") if event.type is EventType.RECOVERY]
    assert events and "interrupted" in (events[0].detail or "")


def test_recovery_reattaches_paused_runs(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    executor = _Executor()
    store = InMemoryRunStore()
    service = _service(make_manifest, store, executor)
    _seed(store, "run-paused", RunState.PAUSED)

    report = RunRecovery(service).run()

    assert report.reattached == ["run-paused"]
    assert executor.reattached == ["run-paused"]
    assert service.get("run-paused").state is RunState.PAUSED
    events = [event for event in service.events("run-paused") if event.type is EventType.RECOVERY]
    assert events and "reattached" in (events[0].detail or "")


def test_recovery_leaves_terminal_and_queued_runs_alone(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    executor = _Executor()
    store = InMemoryRunStore()
    service = _service(make_manifest, store, executor)
    for run_id, state in (
        ("run-queued", RunState.QUEUED),
        ("run-completed", RunState.COMPLETED),
        ("run-failed", RunState.FAILED),
        ("run-cancelled", RunState.CANCELLED),
    ):
        _seed(store, run_id, state)

    report = RunRecovery(service).run()

    assert report == RecoveryReport(reattached=[], failed=[])
    assert executor.reattached == []
    assert service.get("run-queued").state is RunState.QUEUED
    assert service.get("run-completed").state is RunState.COMPLETED


def test_recovery_is_idempotent_for_running_runs(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    store = InMemoryRunStore()
    service = _service(make_manifest, store, _Executor())
    _seed(store, "run-running", RunState.RUNNING)
    recovery = RunRecovery(service)

    recovery.run()
    second = recovery.run()

    assert second.failed == []
    events = [event for event in service.events("run-running") if event.type is EventType.RECOVERY]
    assert len(events) == 1


def test_startup_recovery_runs_during_the_app_lifespan(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    store = InMemoryRunStore()
    service = _service(make_manifest, store, _Executor())
    _seed(store, "run-running", RunState.RUNNING)
    app = create_app(run_service=service)

    with TestClient(app):
        pass

    assert service.get("run-running").state is RunState.FAILED


def test_recovery_skips_runs_whose_workload_is_gone(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    """An orphaned pause must not abort startup recovery (regression: a paused
    run referencing a deleted workload raised WorkloadNotFoundError and killed
    the app lifespan)."""
    store = InMemoryRunStore()
    service = _service(make_manifest, store, _Executor())
    store.save_run(
        Run(
            id="run-ghost",
            workload_id="ghost",
            caller="cli",
            state=RunState.PAUSED,
            context=AdmissionContext.SANDBOX,
            created_at=_NOW,
            updated_at=_NOW,
        )
    )

    report = RunRecovery(service).run()

    assert report.skipped == ["run-ghost"]
    assert report.reattached == []
    assert service.get("run-ghost").state is RunState.PAUSED


def test_paused_run_survives_a_restart_and_resumes(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    store = JsonFileRunStore(tmp_path)
    _seed(store, "run-paused", RunState.PAUSED)

    reloaded = JsonFileRunStore(tmp_path)
    executor = _Executor()
    service = _service(make_manifest, reloaded, executor)
    RunRecovery(service).run()

    resumed = service.intervene("run-paused", InterventionAction.RESUME, actor="cli")

    assert resumed.state is RunState.RUNNING
    assert executor.resumed == ["run-paused"]
