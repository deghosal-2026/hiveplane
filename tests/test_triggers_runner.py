"""Tests for the cron/watch tick runner that fires scheduled triggers (M27/M28)."""

from __future__ import annotations

from datetime import UTC, datetime

from pydantic import JsonValue

from hiveplane.core.run import AdmissionContext, Run, RunState, TriggerOrigin
from hiveplane.fleet.triggers import TriggerRunStatus
from hiveplane.tenancy import TenantContext
from hiveplane.triggers.engine import TriggerEngine
from hiveplane.triggers.limiter import TriggerLimiter
from hiveplane.triggers.runner import TriggerRunner
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


def _cron_spec(**overrides: object) -> TriggerSpec:
    base: dict[str, object] = {
        "id": "nightly",
        "source": "cron",
        "target": {"kind": "workload", "ref": "agent-1"},
        "schedule": "*/5 * * * *",
        "admission_rule": "staging-auto",
    }
    base.update(overrides)
    return TriggerSpec.model_validate(base)


def _runner(
    spec: TriggerSpec, *, active: int = 0
) -> tuple[TriggerRunner, InMemoryTriggerStore, _Submitter]:
    store = InMemoryTriggerStore()
    store.save_trigger(spec)
    submitter = _Submitter()
    engine = TriggerEngine(
        store,
        TriggerLimiter(store, clock=lambda: _NOW),
        submitter,
        clock=lambda: _NOW,
        id_factory=lambda: "ev-1",
    )
    scheduler = TriggerScheduler(store, clock=lambda: _NOW)
    watch = WatchRunner(
        store,
        scheduler,
        engine,
        active_runs=lambda ref: active,
        clock=lambda: _NOW,
    )
    return TriggerRunner(store, scheduler, engine, watch), store, submitter


def test_cron_runner_fires_due_schedule() -> None:
    runner, store, submitter = _runner(_cron_spec())
    decisions = runner.tick()
    assert len(decisions) == 1
    assert decisions[0].status is TriggerRunStatus.SUBMITTED
    assert submitter.calls == 1
    assert store.list_runs("nightly")[0].run_id == "run-1"


def test_watch_runner_tick_fires_watch_trigger() -> None:
    spec = _cron_spec(id="watchdog", source="watch", max_concurrent_runs=1)
    runner, _, submitter = _runner(spec)
    decisions = runner.tick()
    assert len(decisions) == 1
    assert submitter.calls == 1


def test_runner_ignores_disabled_and_unscheduled_triggers() -> None:
    disabled = _cron_spec(id="off", enabled=False)
    runner, _, submitter = _runner(disabled)
    assert runner.tick() == []
    assert submitter.calls == 0
