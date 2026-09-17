"""Tests that RunService records audit entries when an audit log is configured."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane.core.decision import DecisionOutcome
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.admission import AdmissionPipeline
from hiveplane.execution.models import DeliveryRecord, InterventionAction
from hiveplane.execution.service import RunService
from hiveplane.execution.store import InMemoryRunStore
from hiveplane.persistence.audit import InMemoryAuditLog
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore
from test_execution_admission import _Budget, _Cert, _Policy, _Sandbox


class _FanOut:
    def notify(self, run: Run, workload: AgentWorkload) -> list[DeliveryRecord]:
        return []

    def notify_escalation(self, run: Run, workload: AgentWorkload) -> list[DeliveryRecord]:
        return []


def _service(make_manifest: Callable[..., AgentWorkload], audit: InMemoryAuditLog) -> RunService:
    registry = RegistryService(InMemoryRegistryStore())
    registry.create(make_manifest(name="agent-1"))
    return RunService(
        InMemoryRunStore(),
        registry,
        admission=AdmissionPipeline(
            _Cert(True), _Policy(DecisionOutcome.ALLOW), _Budget(True), _Sandbox(False)
        ),
        executor=None,
        fanout=_FanOut(),
        audit=audit,
    )


def test_intervention_is_audited(make_manifest: Callable[..., AgentWorkload]) -> None:
    audit = InMemoryAuditLog(clock=lambda: datetime(2026, 1, 1, tzinfo=UTC))
    service = _service(make_manifest, audit)
    run = service.submit(workload="agent-1", caller="cli", context=AdmissionContext.SANDBOX)
    service.start(run.id, actor="cli")
    service.intervene(run.id, InterventionAction.PAUSE, actor="operator")

    assert "pause" in [record.action for record in audit.records()]
    assert audit.verify() is True


def test_terminal_transition_is_audited(make_manifest: Callable[..., AgentWorkload]) -> None:
    audit = InMemoryAuditLog()
    service = _service(make_manifest, audit)
    run = service.submit(workload="agent-1", caller="cli", context=AdmissionContext.SANDBOX)
    service.start(run.id, actor="cli")
    service.transition(run.id, RunState.COMPLETED, actor="adapter")

    assert any(record.action == "transition" for record in audit.records())
