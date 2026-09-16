"""Tests for sandbox lifecycle through the run service."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane.core.decision import DecisionOutcome
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.admission import AdmissionPipeline
from hiveplane.execution.models import DeliveryRecord
from hiveplane.execution.service import RunService
from hiveplane.execution.store import InMemoryRunStore
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore
from hiveplane.sandbox.manager import InMemorySandboxManager
from hiveplane.sandbox.models import SandboxStatus
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
    make_manifest: Callable[..., AgentWorkload], *, sandbox_required: bool = True
) -> tuple[RunService, InMemorySandboxManager]:
    registry = RegistryService(InMemoryRegistryStore())
    registry.create(make_manifest(name="agent-1"))
    store = InMemoryRunStore()
    manager = InMemorySandboxManager(clock=_clock, id_factory=lambda: "sb-1")
    admission = AdmissionPipeline(
        _Cert(True, "m1"),
        _Policy(DecisionOutcome.ALLOW),
        _Budget(True),
        _Sandbox(sandbox_required),
        clock=_clock,
    )
    service = RunService(
        store,
        registry,
        admission=admission,
        executor=_Executor(),
        fanout=_FanOut(),
        sandbox_runtime=manager,
        clock=_clock,
        id_factory=lambda: "run-1",
    )
    return service, manager


def test_sandboxed_run_provisions_and_destroys(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, manager = _service(make_manifest)
    run = service.submit(workload="agent-1", caller="cli", context=AdmissionContext.SANDBOX)
    assert run.sandbox is True
    running = service.transition(run.id, RunState.RUNNING, actor="scheduler")
    assert running.sandbox_id is not None
    assert manager.status(running.sandbox_id) is SandboxStatus.READY
    completed = service.transition(run.id, RunState.COMPLETED, actor="runtime")
    assert completed.sandbox_id == running.sandbox_id
    assert manager.status(completed.sandbox_id) is SandboxStatus.DESTROYED


def test_non_sandboxed_run_has_no_sandbox(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, manager = _service(make_manifest, sandbox_required=False)
    run = service.submit(workload="agent-1", caller="cli", context=AdmissionContext.PRODUCTION)
    service.transition(run.id, RunState.RUNNING, actor="scheduler")
    assert manager.list_instances() == []
