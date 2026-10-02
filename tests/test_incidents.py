"""Tests for incident mode: fleet halt, drain, broadcast, and recovery (M53)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine

from hiveplane.config import Settings
from hiveplane.core.run import Run
from hiveplane.core.usage import BudgetOutcome, UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.admission import AdmissionPipeline
from hiveplane.incident.models import HaltScope, IncidentRecord
from hiveplane.incident.notify import IncidentBroadcaster, compose_incident_message
from hiveplane.incident.service import (
    IncidentHaltGate,
    IncidentService,
    NoActiveIncidentError,
)
from hiveplane.incident.store import (
    InMemoryIncidentStore,
    PostgresIncidentStore,
    build_incident_store,
)
from hiveplane.persistence.audit import AuditRecord, InMemoryAuditLog
from postgres import ensure_schema

_NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)


def _service(
    *,
    audit: InMemoryAuditLog | None = None,
    notifier: Callable[[IncidentRecord], list[str]] | None = None,
    drain: Callable[[], list[str]] | None = None,
    store: InMemoryIncidentStore | None = None,
) -> IncidentService:
    return IncidentService(
        store or InMemoryIncidentStore(),
        audit=audit,
        notifier=notifier,
        drain=drain,
        clock=lambda: _NOW,
        incident_id_factory=lambda: "inc-1",
    )


def test_pause_records_an_active_fleet_incident() -> None:
    service = _service()

    record = service.pause(actor="alice", reason="prod is down")

    assert record.incident_id == "inc-1"
    assert record.scope is HaltScope.FLEET
    assert record.halted_at == _NOW
    assert record.resumed_at is None
    assert service.active() == record
    assert service.halted() is True


def test_pause_broadcasts_to_owners_and_records_them() -> None:
    recorded: list[IncidentRecord] = []

    def notifier(record: IncidentRecord) -> list[str]:
        recorded.append(record)
        return ["slack:#ops", "pagerduty:oncall"]

    service = _service(notifier=notifier)

    record = service.pause(actor="alice", reason="incident")

    assert record.owners_notified == ["slack:#ops", "pagerduty:oncall"]
    active = service.active()
    assert active is not None
    assert active.owners_notified == ["slack:#ops", "pagerduty:oncall"]
    assert recorded[0].incident_id == "inc-1"


def test_pause_drains_triggers() -> None:
    calls: list[int] = []

    def drain() -> list[str]:
        calls.append(1)
        return ["run-1", "run-2"]

    service = _service(drain=drain)

    service.pause(actor="alice", reason="incident")

    assert calls == [1]


def test_halted_respects_scope() -> None:
    service = _service()
    service.pause(actor="alice", scope=HaltScope.TENANT, scope_ref="acme")

    assert service.halted(tenant_id="acme") is True
    assert service.halted(tenant_id="other") is False

    service.resume(actor="alice")


def test_halted_respects_workload_scope() -> None:
    service = _service()
    service.pause(actor="alice", scope=HaltScope.WORKLOAD, scope_ref="summarizer")

    assert service.halted(workload="summarizer") is True
    assert service.halted(workload="other") is False
    assert service.halted() is False


def test_resume_records_attribution_and_audits() -> None:
    audit = InMemoryAuditLog()
    service = _service(audit=audit)
    service.pause(actor="alice", reason="incident")

    resumed = service.resume(actor="bob")

    assert resumed.resumed_at == _NOW
    assert resumed.resumed_by == "bob"
    assert service.active() is None
    assert service.halted() is False
    actions = [record.action for record in audit.records()]
    assert actions == ["fleet.paused", "fleet.resumed"]
    assert [record.actor for record in audit.records()] == ["alice", "bob"]


def test_resume_lifts_the_drain() -> None:
    calls: list[str] = []

    def drain() -> list[str]:
        calls.append("drain")
        return []

    def undrain() -> None:
        calls.append("undrain")

    service = IncidentService(
        InMemoryIncidentStore(),
        drain=drain,
        undrain=undrain,
        clock=lambda: _NOW,
        incident_id_factory=lambda: "inc-1",
    )
    service.pause(actor="alice")

    service.resume(actor="bob")

    assert calls == ["drain", "undrain"]


def test_resume_without_active_incident_raises() -> None:
    service = _service()

    with pytest.raises(NoActiveIncidentError):
        service.resume(actor="bob")


def test_halted_fails_closed_when_store_is_unreadable() -> None:
    class BrokenStore(InMemoryIncidentStore):
        def active_all(self) -> list[IncidentRecord]:
            raise RuntimeError("database down")

    service = IncidentService(
        BrokenStore(), clock=lambda: _NOW, incident_id_factory=lambda: "inc-1"
    )

    assert service.halted() is True


def test_history_lists_incidents_newest_first() -> None:
    store = InMemoryIncidentStore()
    counter = iter(["inc-1", "inc-2"])
    service = IncidentService(store, clock=lambda: _NOW, incident_id_factory=lambda: next(counter))
    first = service.pause(actor="alice")
    service.resume(actor="alice")
    second = service.pause(actor="bob")

    history = service.history()

    assert [record.incident_id for record in history] == [second.incident_id, first.incident_id]


def test_incident_halt_gate_delegates_to_service() -> None:
    service = _service()
    gate = IncidentHaltGate(service)

    assert gate.halted(tenant_id="default", workload="summarizer") is False
    service.pause(actor="alice")
    assert gate.halted(tenant_id="default", workload="summarizer") is True


def test_admission_refuses_when_fleet_is_halted(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service = _service()
    service.pause(actor="alice", reason="incident")
    pipeline = _pipeline(IncidentHaltGate(service))

    result = pipeline.check(_run(), make_manifest(name="summarizer"))

    assert result.outcome.value == "refused"
    assert "halted" in (result.refused_reason or "")


def test_admission_allows_when_not_halted(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    pipeline = _pipeline(IncidentHaltGate(_service()))

    result = pipeline.check(_run(), make_manifest(name="summarizer"))

    assert result.outcome.value != "refused"


def test_broadcaster_sends_and_reports_targets() -> None:
    sent: list[tuple[str, dict[str, object]]] = []

    class _Transport:
        def send(self, destination: object, message: dict[str, object]) -> None:
            sent.append((destination.type.value, message))  # type: ignore[attr-defined]

    from hiveplane.core.fanout import FanOutDestination, FanOutType

    broadcaster = IncidentBroadcaster(
        {FanOutType.SLACK: _Transport()},  # type: ignore[dict-item]
        [FanOutDestination(type=FanOutType.SLACK, channel="#ops")],
    )
    record = IncidentRecord(
        incident_id="inc-1",
        scope=HaltScope.FLEET,
        trigger="operator",
        reason="prod down",
        actor="alice",
        halted_at=_NOW,
    )

    delivered = broadcaster.notify(record)

    assert delivered == ["#ops"]
    assert sent[0][0] == "slack"
    assert sent[0][1]["type"] == "fleet.halted"


def test_compose_incident_message_contains_scope_and_actor() -> None:
    record = IncidentRecord(
        incident_id="inc-1",
        scope=HaltScope.TENANT,
        scope_ref="acme",
        trigger="operator",
        reason="incident",
        actor="alice",
        halted_at=_NOW,
    )

    message = compose_incident_message(record)

    assert message["type"] == "fleet.halted"
    assert message["scope"] == "tenant"
    assert message["scope_ref"] == "acme"
    assert message["actor"] == "alice"


def test_builder_defaults_to_in_memory() -> None:
    assert isinstance(build_incident_store(Settings()), InMemoryIncidentStore)


def test_builder_selects_postgres() -> None:
    settings = Settings.model_validate({"execution": {"store": "postgres"}})
    assert isinstance(build_incident_store(settings), PostgresIncidentStore)


def test_postgres_incident_round_trip(pg_engine: Engine) -> None:
    ensure_schema(pg_engine)
    store = PostgresIncidentStore(pg_engine)
    store.clear()
    record = IncidentRecord(
        incident_id="inc-1",
        scope=HaltScope.FLEET,
        trigger="operator",
        reason="incident",
        actor="alice",
        halted_at=_NOW,
    )
    store.save(record)

    reopened = PostgresIncidentStore(pg_engine)
    assert reopened.get("inc-1") == record
    assert reopened.active() == record
    assert [r.incident_id for r in reopened.list()] == ["inc-1"]

    resumed = record.model_copy(update={"resumed_at": _NOW, "resumed_by": "bob"})
    reopened.save(resumed)
    assert reopened.active() is None
    store.clear()


def test_service_factory_builds_in_memory() -> None:
    service = IncidentService.build(Settings())
    assert isinstance(service, IncidentService)


# --- helpers ---------------------------------------------------------------


def _pipeline(halt: IncidentHaltGate) -> AdmissionPipeline:
    from hiveplane.core.decision import DecisionOutcome, PolicyDecision
    from hiveplane.core.usage import BudgetCheck, BudgetLevel
    from hiveplane.execution.gates import (
        BudgetGate,
        CertificationGate,
        PolicyGate,
        SandboxGate,
    )
    from hiveplane.registry.models import AdmissionDecision

    class _Cert(CertificationGate):
        def check_admission(
            self, workload: str, context: object, *, ctx: object = None
        ) -> AdmissionDecision:
            return AdmissionDecision(
                workload=workload,
                context=context,  # type: ignore[arg-type]
                admitted=True,
                actual_status="certified",  # type: ignore[arg-type]
                reason="ok",
            )

        def attestation_model(self, workload: str, *, ctx: object = None) -> str | None:
            return None

    class _Policy(PolicyGate):
        def evaluate(self, context: object) -> PolicyDecision:
            return PolicyDecision(
                run_id=context.run_id,  # type: ignore[attr-defined]
                outcome=DecisionOutcome.ALLOW,
                reason="ok",
                rule="allow",
                action_class=None,
                timestamp=_NOW,
                certification_status=None,
            )

    class _Budget(BudgetGate):
        def check(
            self,
            workload: object,
            context: object,
            *,
            ctx: object = None,
        ) -> BudgetCheck:
            return BudgetCheck(
                allowed=True,
                level=BudgetLevel.RUN,
                limit_usd=1.0,
                spent_usd=0.0,
                remaining_usd=1.0,
            )

        def record_usage(
            self, workload: AgentWorkload, report: UsageReport, *, ctx: object = None
        ) -> BudgetOutcome:
            raise NotImplementedError

    class _Sandbox(SandboxGate):
        def required(self, workload: object, context: object, action_class: object) -> bool:
            return False

    return AdmissionPipeline(
        _Cert(), _Policy(), _Budget(), _Sandbox(), halt=halt
    )


def _run() -> Run:
    from hiveplane.core.run import AdmissionContext, RunState

    return Run(
        id="run-1",
        workload_id="summarizer",
        caller="cli",
        state=RunState.QUEUED,
        created_at=_NOW,
        updated_at=_NOW,
        context=AdmissionContext.PRODUCTION,
        tenant_id="default",
    )


def test_audit_record_is_operator_attributed() -> None:
    audit = InMemoryAuditLog()
    service = _service(audit=audit)
    service.pause(actor="alice", reason="incident")

    record: AuditRecord = audit.records()[0]
    assert record.actor == "alice"
    assert record.subject == "inc-1"
