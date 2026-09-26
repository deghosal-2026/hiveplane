"""Tests for the trigger evaluation → run submission engine (M27-07)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from hiveplane.core.run import AdmissionContext, Run, RunState, TriggerOrigin
from hiveplane.execution.errors import RunAdmissionRefusedError
from hiveplane.execution.models import AdmissionCheck, AdmissionOutcome, AdmissionResult
from hiveplane.fleet.triggers import TriggerOutcome, TriggerRunStatus
from hiveplane.tenancy import TenantContext
from hiveplane.triggers.engine import TriggerDisabledError, TriggerEngine
from hiveplane.triggers.limiter import TriggerLimiter
from hiveplane.triggers.schema import TriggerSpec
from hiveplane.triggers.store import InMemoryTriggerStore

_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class FakeSubmitter:
    def __init__(self, error: Exception | None = None) -> None:
        self.calls: list[dict[str, Any]] = []
        self._error = error
        self._count = 0

    def submit(
        self,
        *,
        workload: str,
        caller: str,
        context: AdmissionContext,
        task: dict[str, Any],
        trigger_origin: TriggerOrigin | None,
        require_approval: bool,
        ctx: TenantContext,
    ) -> Run:
        self.calls.append(
            {
                "workload": workload,
                "caller": caller,
                "context": context,
                "task": task,
                "trigger_origin": trigger_origin,
                "require_approval": require_approval,
            }
        )
        if self._error is not None:
            raise self._error
        self._count += 1
        return Run(
            id=f"run-{self._count}",
            workload_id=workload,
            caller=caller,
            state=RunState.QUEUED,
            created_at=_NOW,
            updated_at=_NOW,
            context=context,
            task=task,
            trigger_origin=trigger_origin,
            tenant_id=ctx.tenant_id,
        )


def _spec(**overrides: object) -> TriggerSpec:
    base: dict[str, object] = {
        "id": "t1",
        "source": "webhook",
        "target": {"kind": "workload", "ref": "agent-1"},
        "task_template": {"pr": "{{ event.number }}"},
    }
    base.update(overrides)
    return TriggerSpec.model_validate(base)


def _engine(
    submitter: FakeSubmitter | None = None,
) -> tuple[TriggerEngine, InMemoryTriggerStore, FakeSubmitter]:
    import itertools

    store = InMemoryTriggerStore()
    fake = submitter or FakeSubmitter()
    limiter = TriggerLimiter(store, clock=lambda: _NOW, global_max_per_minute=600)
    counter = itertools.count(1)
    engine = TriggerEngine(
        store,
        limiter,
        fake,
        clock=lambda: _NOW,
        id_factory=lambda: f"ev-{next(counter)}",
    )
    return engine, store, fake


def _refusal(step: str) -> RunAdmissionRefusedError:
    return RunAdmissionRefusedError(
        AdmissionResult(
            run_id="run-x",
            workload="agent-1",
            context=AdmissionContext.PRODUCTION,
            outcome=AdmissionOutcome.REFUSED,
            checks=[AdmissionCheck(step=step, passed=False, reason="no")],
            refused_reason="no",
        )
    )


def test_accept_renders_task_and_submits_run() -> None:
    engine, store, fake = _engine()
    decision = engine.ingest(_spec(), {"number": 42})
    assert decision.outcome is TriggerOutcome.ACCEPTED
    assert decision.status is TriggerRunStatus.SUBMITTED
    assert decision.run_id == "run-1"
    assert fake.calls[0]["task"] == {"pr": 42}
    assert fake.calls[0]["trigger_origin"].event_id == "ev-1"
    events = store.list_events("t1")
    assert [event.outcome for event in events] == [TriggerOutcome.ACCEPTED]


def test_duplicate_dedup_key_starts_one_run() -> None:
    engine, _store, fake = _engine()
    spec = _spec(dedup={"key": "{{ event.number }}", "window_minutes": 30})
    first = engine.ingest(spec, {"number": 42})
    second = engine.ingest(spec, {"number": 42})
    assert first.run_id == "run-1"
    assert second.outcome is TriggerOutcome.DEDUPLICATED
    assert second.run_id is None
    assert len(fake.calls) == 1


def test_cooldown_suppresses_second_event() -> None:
    engine, _store, fake = _engine()
    spec = _spec(cooldown_seconds=60)
    engine.ingest(spec, {"number": 1})
    second = engine.ingest(spec, {"number": 2})
    assert second.outcome is TriggerOutcome.SUPPRESSED_COOLDOWN
    assert len(fake.calls) == 1


def test_rate_limit_rejects_excess() -> None:
    engine, _store, fake = _engine()
    spec = _spec(rate_limit={"max_per_minute": 1, "burst": 0})
    engine.ingest(spec, {"number": 1})
    second = engine.ingest(spec, {"number": 2})
    assert second.outcome is TriggerOutcome.REJECTED_RATE
    assert len(fake.calls) == 1


def test_deny_admission_rule_blocks_without_submitting() -> None:
    engine, _store, fake = _engine()
    decision = engine.ingest(_spec(admission_rule="deny"), {"number": 1})
    assert decision.outcome is TriggerOutcome.ACCEPTED
    assert decision.status is TriggerRunStatus.BLOCKED_ADMISSION
    assert fake.calls == []


def test_staging_auto_submits_to_staging() -> None:
    engine, _, fake = _engine()
    engine.ingest(_spec(admission_rule="staging-auto"), {"number": 1})
    assert fake.calls[0]["context"] is AdmissionContext.STAGING


def test_gated_submits_to_production() -> None:
    engine, _, fake = _engine()
    engine.ingest(_spec(admission_rule="gated"), {"number": 1})
    assert fake.calls[0]["context"] is AdmissionContext.PRODUCTION


def test_certification_refusal_is_blocked_cert() -> None:
    engine, _store, _ = _engine(FakeSubmitter(_refusal("certification")))
    decision = engine.ingest(_spec(admission_rule="gated"), {"number": 1})
    assert decision.status is TriggerRunStatus.BLOCKED_CERT
    assert decision.run_id is None


def test_policy_refusal_is_blocked_admission() -> None:
    engine, _store, _ = _engine(FakeSubmitter(_refusal("policy")))
    decision = engine.ingest(_spec(admission_rule="gated"), {"number": 1})
    assert decision.status is TriggerRunStatus.BLOCKED_ADMISSION


def test_template_overflow_fails_and_parks_in_dlq() -> None:
    engine, store, fake = _engine()
    spec = _spec(task_template={"x": "{{ event.number }}"}, max_field_bytes=1)
    decision = engine.ingest(spec, {"number": 123456})
    assert decision.outcome is TriggerOutcome.FAILED
    assert fake.calls == []
    assert [entry.trigger_id for entry in store.list_dlq()] == ["t1"]


def test_same_event_id_is_idempotent() -> None:
    engine, _store, fake = _engine()
    spec = _spec(dedup={"key": "{{ event.number }}", "window_minutes": 30})
    first = engine.ingest(spec, {"number": 1}, event_id="ev-42")
    second = engine.ingest(spec, {"number": 2}, event_id="ev-42")
    assert first.run_id == "run-1"
    assert second.run_id == "run-1"
    assert len(fake.calls) == 1


def test_disabled_trigger_is_refused() -> None:
    engine, _, _ = _engine()
    with pytest.raises(TriggerDisabledError):
        engine.ingest(_spec(enabled=False), {"number": 1})


def test_gated_requests_approval_and_staging_auto_does_not() -> None:
    engine, _, fake = _engine()
    engine.ingest(_spec(admission_rule="gated"), {"number": 1})
    engine.ingest(_spec(admission_rule="staging-auto"), {"number": 2})
    assert fake.calls[0]["require_approval"] is True
    assert fake.calls[1]["require_approval"] is False


def test_suppression_reason_and_audit_are_recorded() -> None:
    from hiveplane.persistence.audit import InMemoryAuditLog

    store = InMemoryTriggerStore()
    audit = InMemoryAuditLog(clock=lambda: _NOW)
    limiter = TriggerLimiter(store, clock=lambda: _NOW)
    counter = __import__("itertools").count(1)
    engine = TriggerEngine(
        store,
        limiter,
        FakeSubmitter(),
        clock=lambda: _NOW,
        id_factory=lambda: f"ev-{next(counter)}",
        audit=audit,
    )
    spec = _spec(cooldown_seconds=60)
    engine.ingest(spec, {"number": 1})
    engine.ingest(spec, {"number": 2})
    events = store.list_events("t1")
    assert events[-1].outcome is TriggerOutcome.SUPPRESSED_COOLDOWN
    assert events[-1].reason == "within cooldown window"
    actions = [record.action for record in audit.records()]
    assert "trigger.event.suppressed_cooldown" in actions
