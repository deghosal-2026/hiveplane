"""Tests for the run store."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hiveplane.core.event import EventType, RunEvent
from hiveplane.core.fanout import FanOutType
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.usage import UsageReport
from hiveplane.execution.errors import RunNotFoundError
from hiveplane.execution.models import (
    AdmissionOutcome,
    AdmissionResult,
    DeliveryRecord,
    DeliveryStatus,
)
from hiveplane.execution.store import InMemoryRunStore


def _now() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def _run(run_id: str = "run-1", state: RunState = RunState.QUEUED, **overrides: object) -> Run:
    data: dict[str, object] = {
        "id": run_id,
        "workload_id": "agent-1",
        "caller": "cli",
        "state": state,
        "created_at": _now(),
        "updated_at": _now(),
    }
    data.update(overrides)
    return Run.model_validate(data)


def test_save_and_get_run_round_trips() -> None:
    store = InMemoryRunStore()
    store.save_run(_run())
    fetched = store.get_run("run-1")
    assert fetched is not None
    assert fetched.state is RunState.QUEUED


def test_get_run_missing_returns_none() -> None:
    assert InMemoryRunStore().get_run("nope") is None


def test_get_run_returns_a_copy() -> None:
    store = InMemoryRunStore()
    store.save_run(_run())
    fetched = store.get_run("run-1")
    assert fetched is not None
    fetched.state = RunState.FAILED
    assert store.get_run("run-1").state is RunState.QUEUED  # type: ignore[union-attr]


def test_events_are_ordered_and_copied() -> None:
    store = InMemoryRunStore()
    store.save_run(_run())
    store.add_event(
        RunEvent(
            run_id="run-1",
            sequence=1,
            type=EventType.STATE_CHANGE,
            actor="a",
            timestamp=_now(),
            from_state=RunState.QUEUED,
            to_state=RunState.RUNNING,
        )
    )
    store.add_event(
        RunEvent(run_id="run-1", sequence=0, type=EventType.ADMISSION, actor="a", timestamp=_now())
    )
    assert [e.sequence for e in store.list_events("run-1")] == [0, 1]


def test_event_for_unknown_run_raises() -> None:
    with pytest.raises(RunNotFoundError):
        InMemoryRunStore().add_event(
            RunEvent(
                run_id="nope", sequence=0, type=EventType.ADMISSION, actor="a", timestamp=_now()
            )
        )


def test_list_runs_filters() -> None:
    store = InMemoryRunStore()
    store.save_run(_run("run-1", workload_id="agent-1"))
    store.save_run(_run("run-2", workload_id="agent-2", state=RunState.RUNNING))
    assert {r.id for r in store.list_runs(workload="agent-2")} == {"run-2"}
    assert {r.id for r in store.list_runs(state=RunState.QUEUED)} == {"run-1"}


def test_admission_and_usage_and_delivery_round_trip() -> None:
    store = InMemoryRunStore()
    store.save_run(_run())
    store.save_admission(
        AdmissionResult(
            run_id="run-1",
            workload="agent-1",
            context=AdmissionContext.SANDBOX,
            outcome=AdmissionOutcome.SANDBOX_ONLY,
        )
    )
    store.add_usage(
        UsageReport(
            run_id="run-1",
            input_tokens=10,
            output_tokens=5,
            tool_calls=1,
            cost_usd=0.01,
            timestamp=_now(),
        )
    )
    store.add_delivery(
        DeliveryRecord(
            run_id="run-1",
            destination_type=FanOutType.WEBHOOK,
            target="https://example.test/hook",
            status=DeliveryStatus.DELIVERED,
            attempts=1,
            timestamp=_now(),
        )
    )
    admission = store.get_admission("run-1")
    assert admission is not None
    assert admission.outcome is AdmissionOutcome.SANDBOX_ONLY
    assert store.list_usage("run-1")[0].total_tokens == 15
    assert store.list_deliveries("run-1")[0].status is DeliveryStatus.DELIVERED
