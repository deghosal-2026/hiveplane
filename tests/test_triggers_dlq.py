"""Tests for dead-letter queue replay (M28-07)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import JsonValue

from hiveplane.core.run import AdmissionContext, Run, RunState, TriggerOrigin
from hiveplane.fleet.triggers import (
    TriggerDlqEntry,
    TriggerOutcome,
    TriggerRunStatus,
)
from hiveplane.tenancy import TenantContext
from hiveplane.triggers.engine import (
    DlqEntryNotFoundError,
    TriggerEngine,
)
from hiveplane.triggers.limiter import TriggerLimiter
from hiveplane.triggers.schema import TriggerSpec
from hiveplane.triggers.store import InMemoryTriggerStore

_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


class _Submitter:
    def __init__(self) -> None:
        self.calls = 0

    def submit(
        self,
        *,
        workload: str,
        caller: str,
        context: AdmissionContext,
        task: dict[str, JsonValue],
        trigger_origin: TriggerOrigin | None,
        require_approval: bool,
        ctx: TenantContext,
    ) -> Run:
        self.calls += 1
        return Run(
            id=f"run-{self.calls}",
            workload_id=workload,
            caller=caller,
            state=RunState.QUEUED,
            created_at=_NOW,
            updated_at=_NOW,
            context=context,
            tenant_id=ctx.tenant_id,
        )


def _spec() -> TriggerSpec:
    return TriggerSpec.model_validate(
        {
            "id": "t1",
            "source": "webhook",
            "target": {"kind": "workload", "ref": "agent-1"},
            "task_template": {"pr": "{{ event.number }}"},
            "dedup": {"key": "{{ event.number }}"},
        }
    )


def _engine() -> tuple[TriggerEngine, InMemoryTriggerStore, _Submitter]:
    store = InMemoryTriggerStore()
    submitter = _Submitter()
    counter = __import__("itertools").count(1)
    engine = TriggerEngine(
        store,
        TriggerLimiter(store, clock=lambda: _NOW),
        submitter,
        clock=lambda: _NOW,
        id_factory=lambda: f"ev-{next(counter)}",
    )
    return engine, store, submitter


def _entry(entry_id: str = "dlq-1", *, event_id: str = "ev-orig") -> TriggerDlqEntry:
    return TriggerDlqEntry(
        entry_id=entry_id,
        trigger_id="t1",
        event_id=event_id,
        payload={"number": 7},
        headers={},
        failure_reason="execution unavailable",
        attempts=3,
        created_at=_NOW,
    )


def test_replay_re_drives_the_original_payload() -> None:
    engine, store, submitter = _engine()
    store.save_trigger(_spec())
    store.add_dlq(_entry())
    decision = engine.replay_dlq("dlq-1")
    assert decision.outcome is TriggerOutcome.ACCEPTED
    assert decision.status is TriggerRunStatus.SUBMITTED
    assert submitter.calls == 1
    replayed = store.get_dlq("dlq-1")
    assert replayed is not None and replayed.replayed_at is not None


def test_replay_re_verifies_dedup() -> None:
    engine, store, submitter = _engine()
    store.save_trigger(_spec())
    store.add_dlq(_entry())
    first = engine.replay_dlq("dlq-1")
    assert first.run_id is not None
    # A second DLQ entry with the same payload dedups against the first run's event.
    store.add_dlq(_entry("dlq-2", event_id="ev-orig-2"))
    second = engine.replay_dlq("dlq-2")
    assert second.outcome is TriggerOutcome.DEDUPLICATED
    assert submitter.calls == 1


def test_replay_missing_entry_raises() -> None:
    engine, _, _ = _engine()
    with pytest.raises(DlqEntryNotFoundError):
        engine.replay_dlq("missing")
