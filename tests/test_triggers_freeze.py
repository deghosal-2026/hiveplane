"""Tests for freeze windows: suppression and drain (M28-05)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine

from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.execution.models import InterventionAction
from hiveplane.fleet.triggers import TriggerOutcome
from hiveplane.tenancy import TenantContext
from hiveplane.triggers.freeze import (
    FreezeScope,
    FreezeService,
    FreezeSpec,
    InMemoryFreezeStore,
    PostgresFreezeStore,
)
from hiveplane.triggers.schema import TriggerSpec
from postgres import reset_database

_NOW = datetime(2026, 1, 1, 12, 0, tzinfo=UTC)


def _freeze(**overrides: object) -> FreezeSpec:
    base: dict[str, object] = {
        "freeze_id": "fz-1",
        "scope": "tenant",
        "starts_at": _NOW - timedelta(minutes=5),
        "ends_at": _NOW + timedelta(minutes=5),
        "declared_by": "operator",
    }
    base.update(overrides)
    return FreezeSpec.model_validate(base)


def _spec() -> TriggerSpec:
    return TriggerSpec.model_validate(
        {
            "id": "t1",
            "source": "webhook",
            "target": {"kind": "workload", "ref": "agent-1"},
        }
    )


def _run(run_id: str, state: RunState, *, workload: str = "agent-1") -> Run:
    return Run(
        id=run_id,
        workload_id=workload,
        caller="trigger",
        state=state,
        created_at=_NOW,
        updated_at=_NOW,
        context=AdmissionContext.STAGING,
    )


class _Runs:
    def __init__(self, runs: list[Run]) -> None:
        self.runs = runs
        self.interventions: list[tuple[str, InterventionAction]] = []

    def list_runs(self, *, ctx: TenantContext) -> list[Run]:
        return list(self.runs)

    def intervene(
        self, run_id: str, action: InterventionAction, *, actor: str, ctx: TenantContext
    ) -> Run:
        self.interventions.append((run_id, action))
        run = next(r for r in self.runs if r.id == run_id)
        new_state = RunState.PAUSED if action is InterventionAction.PAUSE else RunState.CANCELLED
        return run.model_copy(update={"state": new_state})


def test_freeze_covers_scopes() -> None:
    assert _freeze(scope="tenant").covers(workload="agent-1", team="platform")
    assert _freeze(scope="workload", scope_ref="agent-1").covers(
        workload="agent-1", team="platform"
    )
    assert not _freeze(scope="workload", scope_ref="agent-1").covers(
        workload="agent-2", team="platform"
    )
    assert _freeze(scope="team", scope_ref="platform").covers(
        workload="agent-1", team="platform"
    )
    assert not _freeze(scope="team", scope_ref="platform").covers(
        workload="agent-1", team="ops"
    )


def test_freeze_active_window() -> None:
    assert _freeze().active_at(_NOW)
    assert not _freeze().active_at(_NOW + timedelta(hours=1))
    assert _freeze(ends_at=None).active_at(_NOW + timedelta(days=365))


def test_service_declare_lift_and_active_for() -> None:
    service = FreezeService(InMemoryFreezeStore(), clock=lambda: _NOW)
    service.declare(_freeze(scope="workload", scope_ref="agent-1"))
    assert service.active_for("agent-1", team="platform") is not None
    assert service.active_for("agent-2", team="platform") is None
    assert service.lift("fz-1") is True
    assert service.active_for("agent-1", team="platform") is None


def test_drain_pauses_running_runs_gracefully() -> None:
    service = FreezeService(InMemoryFreezeStore(), clock=lambda: _NOW)
    runs = _Runs([_run("run-1", RunState.RUNNING), _run("run-2", RunState.QUEUED)])
    acted = service.drain(runs, _freeze(), team=None, ctx=TenantContext(tenant_id="default"))
    assert acted == ["run-1"]
    assert runs.interventions == [("run-1", InterventionAction.PAUSE)]


def test_drain_abort_stops_running_and_queued() -> None:
    service = FreezeService(InMemoryFreezeStore(), clock=lambda: _NOW)
    runs = _Runs([_run("run-1", RunState.RUNNING), _run("run-2", RunState.QUEUED)])
    acted = service.drain(
        runs,
        _freeze(drain="abort"),
        team=None,
        ctx=TenantContext(tenant_id="default"),
    )
    assert acted == ["run-1", "run-2"]
    assert [action for _, action in runs.interventions] == [
        InterventionAction.STOP,
        InterventionAction.STOP,
    ]


def test_drain_respects_workload_scope() -> None:
    service = FreezeService(InMemoryFreezeStore(), clock=lambda: _NOW)
    runs = _Runs(
        [
            _run("run-1", RunState.RUNNING, workload="agent-1"),
            _run("run-2", RunState.RUNNING, workload="agent-2"),
        ]
    )
    acted = service.drain(
        runs,
        _freeze(scope="workload", scope_ref="agent-1"),
        team=None,
        ctx=TenantContext(tenant_id="default"),
    )
    assert acted == ["run-1"]


def test_postgres_freeze_store_round_trips(pg_engine: Engine) -> None:
    reset_database(pg_engine)
    store = PostgresFreezeStore(pg_engine)
    service = FreezeService(store, clock=lambda: _NOW)
    service.declare(_freeze(scope="workload", scope_ref="agent-1"))
    loaded = store.get("fz-1")
    assert loaded is not None and loaded.scope is FreezeScope.WORKLOAD
    assert [f.freeze_id for f in store.list_all()] == ["fz-1"]
    assert service.active_for("agent-1", team="platform") is not None
    assert service.lift("fz-1") is True
    assert store.get("fz-1") is None


def test_engine_suppresses_during_freeze() -> None:
    from hiveplane.triggers.engine import TriggerEngine
    from hiveplane.triggers.limiter import TriggerLimiter
    from hiveplane.triggers.store import InMemoryTriggerStore

    class _NoSubmit:
        def submit(self, **kwargs: object) -> Run:  # pragma: no cover - never called
            raise AssertionError("should not submit during a freeze")

    store = InMemoryTriggerStore()
    service = FreezeService(InMemoryFreezeStore(), clock=lambda: _NOW)
    service.declare(_freeze(scope="workload", scope_ref="agent-1"))
    engine = TriggerEngine(
        store,
        TriggerLimiter(store, clock=lambda: _NOW),
        _NoSubmit(),
        clock=lambda: _NOW,
        id_factory=lambda: "ev-1",
        freeze_check=lambda spec, ctx: (
            "tenant freeze active" if service.active_for(spec.target.ref, team=None) else None
        ),
    )
    decision = engine.ingest(_spec(), {"number": 1})
    assert decision.outcome is TriggerOutcome.SUPPRESSED_FREEZE
    assert store.list_events("t1")[0].reason == "tenant freeze active"
