"""Tests for the timezone-aware cron scheduler and missed-schedule policy (M27-03)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from hiveplane.fleet.triggers import TriggerRun, TriggerRunStatus
from hiveplane.triggers.scheduler import TriggerScheduler
from hiveplane.triggers.schema import TriggerSpec
from hiveplane.triggers.store import InMemoryTriggerStore

_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _spec(**overrides: object) -> TriggerSpec:
    base: dict[str, object] = {
        "id": "nightly",
        "source": "cron",
        "target": {"kind": "workload", "ref": "agent-1"},
        "schedule": "*/5 * * * *",
    }
    base.update(overrides)
    return TriggerSpec.model_validate(base)


def _run(at: datetime, run_id: str = "run-1") -> TriggerRun:
    return TriggerRun(
        trigger_id="nightly",
        event_id=f"ev-{run_id}",
        run_id=run_id,
        fired_at=at,
        status=TriggerRunStatus.SUBMITTED,
    )


def test_due_fires_on_schedule() -> None:
    store = InMemoryTriggerStore()
    scheduler = TriggerScheduler(store, clock=lambda: _NOW)
    due = scheduler.due(_spec(), last_fired=_NOW - timedelta(minutes=5))
    assert due == [_NOW]


def test_nothing_due_when_no_tick() -> None:
    store = InMemoryTriggerStore()
    scheduler = TriggerScheduler(store, clock=lambda: datetime(2026, 1, 1, 12, 2, tzinfo=UTC))
    assert scheduler.due(_spec(), last_fired=_NOW) == []


def test_skip_policy_drops_a_missed_tick() -> None:
    now = datetime(2026, 1, 1, 12, 5, tzinfo=UTC)
    scheduler = TriggerScheduler(store=InMemoryTriggerStore(), clock=lambda: now)
    spec = _spec(schedule="0 12 * * *", missed_schedule_policy="skip")
    # The 12:00 tick is five minutes stale: outside the current tick window.
    assert scheduler.due(spec, last_fired=now - timedelta(days=1)) == []


def test_skip_policy_fires_when_tick_is_current() -> None:
    scheduler = TriggerScheduler(store=InMemoryTriggerStore(), clock=lambda: _NOW)
    spec = _spec(missed_schedule_policy="skip")
    assert scheduler.due(spec, last_fired=_NOW - timedelta(minutes=5)) == [_NOW]


def test_catch_up_policy_fires_once_for_many_missed() -> None:
    scheduler = TriggerScheduler(store=InMemoryTriggerStore(), clock=lambda: _NOW)
    spec = _spec(missed_schedule_policy="catch_up")
    due = scheduler.due(spec, last_fired=_NOW - timedelta(hours=1))
    assert len(due) == 1
    assert due[0] == _NOW


def test_catch_up_all_policy_fires_every_missed_interval_bounded() -> None:
    scheduler = TriggerScheduler(
        store=InMemoryTriggerStore(), clock=lambda: _NOW, max_catch_up=3
    )
    spec = _spec(missed_schedule_policy="catch_up_all")
    due = scheduler.due(spec, last_fired=_NOW - timedelta(hours=1))
    assert len(due) == 3
    assert due == sorted(due)


def test_due_uses_last_recorded_run() -> None:
    store = InMemoryTriggerStore()
    store.add_run(_run(_NOW - timedelta(minutes=5)))
    scheduler = TriggerScheduler(store, clock=lambda: _NOW)
    assert scheduler.due(_spec()) == [_NOW]


def test_timezone_aware_schedule() -> None:
    # 09:00 New York == 14:00 UTC in January.
    now = datetime(2026, 1, 1, 14, 0, tzinfo=UTC)
    scheduler = TriggerScheduler(store=InMemoryTriggerStore(), clock=lambda: now)
    spec = _spec(schedule="0 9 * * *", timezone="America/New_York")
    assert scheduler.due(spec, last_fired=now - timedelta(days=1)) == [now]
