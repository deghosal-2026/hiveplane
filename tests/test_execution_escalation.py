"""Tests for run escalation, approvals, and escalation fan-out."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

from hiveplane.core.approval import ApprovalRecord
from hiveplane.core.decision import DecisionOutcome
from hiveplane.core.fanout import FanOutDestination, FanOutType
from hiveplane.core.run import AdmissionContext, RunState
from hiveplane.core.usage import UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.admission import AdmissionPipeline
from hiveplane.execution.fanout import FanOutService
from hiveplane.execution.models import RunContext
from hiveplane.execution.service import RunService
from hiveplane.execution.store import InMemoryRunStore
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore
from test_execution_admission import _Budget, _Cert, _Policy, _Sandbox


def _clock() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


class _Executor:
    def start(self, context: RunContext) -> None:
        pass

    def pause(self, run_id: str) -> bool:
        return True

    def resume(self, run_id: str) -> bool:
        return True

    def cancel(self, run_id: str) -> None:
        pass

    def status(self, run_id: str) -> RunState:
        return RunState.PAUSED

    def usage(self, run_id: str) -> UsageReport | None:
        return None


class _Approvals:
    def __init__(self) -> None:
        self.requested: list[tuple[str, str, str, str]] = []

    def request(
        self,
        *,
        run_id: str,
        workload: str,
        rule: str,
        reason: str,
        action_class: object | None = None,
    ) -> ApprovalRecord:
        self.requested.append((run_id, workload, rule, reason))
        return ApprovalRecord(
            approval_id="ap-1",
            run_id=run_id,
            workload=workload,
            rule=rule,
            reason=reason,
            requested_at=_clock(),
        )

    def list(
        self,
        *,
        status: object | None = None,
        workload: str | None = None,
        run_id: str | None = None,
    ) -> list[ApprovalRecord]:
        """No approvals are granted in these tests."""
        return []


class _Recorder:
    def __init__(self) -> None:
        self.sent: list[dict[str, Any]] = []

    def send(self, destination: FanOutDestination, message: dict[str, Any]) -> None:
        self.sent.append(message)


def _service(
    make_manifest: Callable[..., AgentWorkload],
    *,
    approvals: _Approvals | None = None,
    escalation_destinations: list[dict[str, object]] | None = None,
) -> tuple[RunService, InMemoryRunStore, _Recorder]:
    registry = RegistryService(InMemoryRegistryStore())
    workload = make_manifest(
        name="agent-1",
        fan_out={"on_escalation": escalation_destinations or []},
    )
    registry.create(workload)
    store = InMemoryRunStore()
    recorder = _Recorder()
    fanout = FanOutService(store, {FanOutType.SLACK: recorder}, clock=_clock)
    admission = AdmissionPipeline(
        _Cert(True, "m1"),
        _Policy(DecisionOutcome.ESCALATE),
        _Budget(True),
        _Sandbox(False),
        clock=_clock,
    )
    ids = iter(["run-1", "run-2"])
    service = RunService(
        store,
        registry,
        admission=admission,
        executor=_Executor(),
        fanout=fanout,
        approvals=approvals,
        clock=_clock,
        id_factory=lambda: next(ids),
    )
    return service, store, recorder


def test_escalation_requests_approval(make_manifest: Callable[..., AgentWorkload]) -> None:
    approvals = _Approvals()
    service, _, _ = _service(make_manifest, approvals=approvals)
    run = service.submit(
        workload="agent-1", caller="cli", context=AdmissionContext.PRODUCTION, model_identity="m1"
    )
    assert run.state is RunState.PAUSED
    assert approvals.requested
    assert approvals.requested[0][0] == "run-1"


def test_escalation_fans_out_to_slack(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, _, recorder = _service(
        make_manifest, escalation_destinations=[{"type": "slack", "channel": "#ops"}]
    )
    service.submit(
        workload="agent-1", caller="cli", context=AdmissionContext.PRODUCTION, model_identity="m1"
    )
    assert recorder.sent[0]["state"] == "paused"


def test_fail_records_reason(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, _, _ = _service(make_manifest)
    run = service.submit(
        workload="agent-1", caller="cli", context=AdmissionContext.PRODUCTION, model_identity="m1"
    )
    failed = service.fail(run.id, actor="alice", reason="too risky")
    assert failed.state is RunState.FAILED
    assert failed.failure_reason == "too risky"
    assert service.events(run.id)[-1].to_state is RunState.FAILED


def test_transition_can_set_failure_reason(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, _, _ = _service(make_manifest)
    run = service.submit(
        workload="agent-1", caller="cli", context=AdmissionContext.PRODUCTION, model_identity="m1"
    )
    service.transition(run.id, RunState.RUNNING, actor="scheduler")
    failed = service.transition(run.id, RunState.FAILED, actor="runtime", failure_reason="boom")
    assert failed.failure_reason == "boom"
