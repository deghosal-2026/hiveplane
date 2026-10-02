"""Tests for watch mode: scheduled 24/7 operators with concurrency guard (M28-03)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from pydantic import JsonValue

from hiveplane.core.run import AdmissionContext, Run, RunState, TriggerOrigin
from hiveplane.fleet.triggers import TriggerOutcome
from hiveplane.tenancy import TenantContext
from hiveplane.triggers.engine import TriggerEngine
from hiveplane.triggers.limiter import TriggerLimiter
from hiveplane.triggers.scheduler import TriggerScheduler
from hiveplane.triggers.schema import TriggerSpec
from hiveplane.triggers.store import InMemoryTriggerStore
from hiveplane.triggers.watch import WatchRunner

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


def _spec(**overrides: object) -> TriggerSpec:
    base: dict[str, object] = {
        "id": "watchdog",
        "source": "watch",
        "target": {"kind": "workload", "ref": "agent-1"},
        "schedule": "*/5 * * * *",
        "max_concurrent_runs": 1,
    }
    base.update(overrides)
    return TriggerSpec.model_validate(base)


def _runner(active: int) -> tuple[WatchRunner, InMemoryTriggerStore, _Submitter]:
    store = InMemoryTriggerStore()
    submitter = _Submitter()
    engine = TriggerEngine(
        store,
        TriggerLimiter(store, clock=lambda: _NOW),
        submitter,
        clock=lambda: _NOW,
        id_factory=lambda: "ev-1",
    )
    scheduler = TriggerScheduler(store, clock=lambda: _NOW)
    runner = WatchRunner(
        store,
        scheduler,
        engine,
        active_runs=lambda ref: active,
        clock=lambda: _NOW,
        id_factory=lambda: "watch-ev-1",
    )
    return runner, store, submitter


def test_watch_tick_fires_when_idle() -> None:
    runner, _, submitter = _runner(active=0)
    decisions = runner.tick(_spec(), last_fired=_NOW - timedelta(minutes=5))
    assert len(decisions) == 1
    assert submitter.calls == 1


def test_watch_tick_skips_when_concurrency_reached() -> None:
    runner, store, submitter = _runner(active=1)
    decisions = runner.tick(_spec(), last_fired=_NOW - timedelta(minutes=5))
    assert decisions == []
    assert submitter.calls == 0
    events = store.list_events("watchdog")
    assert events[-1].outcome is TriggerOutcome.SKIPPED_CONCURRENT


def test_non_watch_source_does_nothing() -> None:
    runner, _, submitter = _runner(active=0)
    assert runner.tick(_spec(source="cron"), last_fired=_NOW) == []
    assert submitter.calls == 0
