"""Tests for RunService as a reporting seam."""

from __future__ import annotations

from collections.abc import Callable

from pydantic import JsonValue

from hiveplane.adapters.base import AdapterRunExecutor
from hiveplane.adapters.reporter import RunReporter
from hiveplane.core.decision import DecisionOutcome
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.admission import AdmissionPipeline
from hiveplane.execution.models import DeliveryRecord, RunContext
from hiveplane.execution.service import RunService
from hiveplane.execution.store import InMemoryRunStore
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore
from test_execution_admission import _Budget, _Cert, _Policy, _Sandbox


class _FanOut:
    def notify(self, run: Run, workload: AgentWorkload) -> list[DeliveryRecord]:
        return []

    def notify_escalation(self, run: Run, workload: AgentWorkload) -> list[DeliveryRecord]:
        return []


class _CapturingExecutor:
    def __init__(self) -> None:
        self.started: list[str] = []

    def start(self, context: RunContext) -> None:
        self.started.append(context.run.id)

    def pause(self, run_id: str) -> bool:
        return True

    def resume(self, run_id: str) -> bool:
        return True

    def cancel(self, run_id: str) -> None:
        return None

    def status(self, run_id: str) -> RunState:
        return RunState.RUNNING

    def usage(self, run_id: str) -> None:
        return None


def _service(make_manifest: Callable[..., AgentWorkload]) -> RunService:
    registry = RegistryService(InMemoryRegistryStore())
    workload = make_manifest(name="agent-1")
    registry.create(workload)
    return RunService(
        InMemoryRunStore(),
        registry,
        admission=AdmissionPipeline(
            _Cert(True),
            _Policy(DecisionOutcome.ALLOW),
            _Budget(True),
            _Sandbox(False),
        ),
        executor=None,
        fanout=_FanOut(),
    )


def test_run_service_satisfies_run_reporter(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    assert isinstance(_service(make_manifest), RunReporter)


def test_attach_executor_uses_the_new_executor(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service = _service(make_manifest)
    executor = _CapturingExecutor()
    service.attach_executor(executor)
    run = service.submit(
        workload="agent-1", caller="cli", context=AdmissionContext.SANDBOX
    )
    service.start(run.id, actor="cli")
    assert executor.started == [run.id]


def test_transition_persists_a_result(make_manifest: Callable[..., AgentWorkload]) -> None:
    service = _service(make_manifest)
    run = service.submit(workload="agent-1", caller="cli", context=AdmissionContext.SANDBOX)
    service.start(run.id, actor="cli")
    updated = service.transition(
        run.id, RunState.COMPLETED, actor="adapter", result={"ok": True}
    )
    assert updated.result == {"ok": True}
    assert service.get(run.id).result == {"ok": True}


def test_adapter_run_executor_start_delegates(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    class _Adapter:
        def __init__(self) -> None:
            self.submitted: list[RunContext] = []

        def register(self, workload: AgentWorkload) -> None:
            return None

        def submit(self, context: RunContext) -> None:
            self.submitted.append(context)

        def pause(self, run_id: str) -> bool:
            return True

        def resume(self, run_id: str) -> bool:
            return True

        def cancel(self, run_id: str) -> None:
            return None

        def status(self, run_id: str) -> RunState:
            return RunState.RUNNING

        def usage(self, run_id: str) -> None:
            return None

        def tool_calls(self, run_id: str) -> list[JsonValue]:
            return []

    adapter = _Adapter()
    executor = AdapterRunExecutor(adapter)  # type: ignore[arg-type]
    service = _service(make_manifest)
    service.attach_executor(executor)
    run = service.submit(workload="agent-1", caller="cli", context=AdmissionContext.SANDBOX)
    service.start(run.id, actor="cli")
    assert [ctx.run.id for ctx in adapter.submitted] == [run.id]
