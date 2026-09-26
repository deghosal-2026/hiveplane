"""Reconcile store tests: in-memory and Postgres (M26-04)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine

from hiveplane.fleet.reconcile import (
    DesiredSpec,
    DriftRecord,
    DriftResolution,
    ReconcileState,
    ReconcileStatus,
    SpecKind,
    SpecSource,
)
from hiveplane.reconcile.models import (
    ActionKind,
    ActionResult,
    ActionStatus,
    ReconcileMode,
    ReconcileOutcome,
    ReconcileRun,
)
from hiveplane.reconcile.store import (
    InMemoryReconcileStore,
    PostgresReconcileStore,
    ReconcileStore,
)
from postgres import reset_database

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _spec(name: str, hash_: str = "c" * 64, source_id: str = "git-main") -> DesiredSpec:
    return DesiredSpec(
        spec_id=f"workload/{name}",
        source_id=source_id,
        source=SpecSource.GIT,
        kind=SpecKind.WORKLOAD,
        name=name,
        revision="rev-1",
        content_hash=hash_,
        spec={"kind": "workload"},
        updated_at=_NOW,
    )


def _state(status: ReconcileStatus = ReconcileStatus.IN_SYNC) -> ReconcileState:
    return ReconcileState(
        source_id="git-main",
        source=SpecSource.GIT,
        last_revision="rev-1",
        last_observed_hash="d" * 64,
        last_reconcile_at=_NOW,
        status=status,
    )


def _drift(drift_id: str = "dr-1") -> DriftRecord:
    return DriftRecord(
        drift_id=drift_id,
        object_ref="agent-1",
        field="spec.certification.production_threshold",
        desired_value=0.9,
        observed_value=0.8,
        detected_at=_NOW,
    )


def _run(run_id: str = "rr-1", source_id: str = "git-main") -> ReconcileRun:
    return ReconcileRun(
        run_id=run_id,
        source_id=source_id,
        source=SpecSource.GIT,
        revision="rev-1",
        mode=ReconcileMode.APPLY,
        started_at=_NOW,
        finished_at=_NOW,
        outcome=ReconcileOutcome.APPLIED,
        actions=[
            ActionResult(
                action_id="act-1",
                kind=ActionKind.REGISTER_WORKLOAD,
                object_kind=SpecKind.WORKLOAD,
                object_ref="agent-1",
                status=ActionStatus.APPLIED,
            )
        ],
    )


@pytest.fixture
def store() -> ReconcileStore:
    return InMemoryReconcileStore()


def test_replace_source_specs_is_atomic_and_idempotent(store: ReconcileStore) -> None:
    store.replace_source_specs("git-main", [_spec("a"), _spec("b")])
    assert [spec.name for spec in store.list_specs("git-main")] == ["a", "b"]
    store.replace_source_specs("git-main", [_spec("b")])
    assert [spec.name for spec in store.list_specs("git-main")] == ["b"]
    store.replace_source_specs("git-main", [_spec("b")])
    assert [spec.name for spec in store.list_specs("git-main")] == ["b"]


def test_specs_are_scoped_by_source(store: ReconcileStore) -> None:
    store.replace_source_specs("git-main", [_spec("a")])
    store.replace_source_specs("git-other", [_spec("b", source_id="git-other")])
    assert [spec.name for spec in store.list_specs("git-main")] == ["a"]
    assert [spec.name for spec in store.list_specs("git-other")] == ["b"]
    assert {spec.name for spec in store.list_specs()} == {"a", "b"}


def test_state_round_trips(store: ReconcileStore) -> None:
    assert store.get_state("git-main") is None
    store.save_state(_state())
    loaded = store.get_state("git-main")
    assert loaded is not None
    assert loaded.status is ReconcileStatus.IN_SYNC
    assert [state.source_id for state in store.list_states()] == ["git-main"]


def test_drift_round_trips_and_resolution_updates(store: ReconcileStore) -> None:
    store.add_drift(_drift())
    drift = store.list_drift(object_ref="agent-1")
    assert len(drift) == 1
    resolved = drift[0].model_copy(update={"resolution": DriftResolution.DECLARED_WINS})
    store.save_drift(resolved)
    assert store.list_drift()[0].resolution is DriftResolution.DECLARED_WINS
    assert store.list_drift(object_ref="missing") == []


def test_runs_round_trip_and_order(store: ReconcileStore) -> None:
    store.add_run(_run("rr-1"))
    store.add_run(_run("rr-2", source_id="git-other"))
    assert store.get_run("rr-1") is not None
    assert store.get_run("missing") is None
    assert [run.run_id for run in store.list_runs()] == ["rr-1", "rr-2"]
    assert [run.run_id for run in store.list_runs(source_id="git-other")] == ["rr-2"]


def test_clear_removes_everything(store: ReconcileStore) -> None:
    store.replace_source_specs("git-main", [_spec("a")])
    store.save_state(_state())
    store.add_drift(_drift())
    store.add_run(_run())
    store.clear()
    assert store.list_specs() == []
    assert store.list_states() == []
    assert store.list_drift() == []
    assert store.list_runs() == []


def test_postgres_store_round_trips(pg_engine: Engine) -> None:
    reset_database(pg_engine)
    store = PostgresReconcileStore(pg_engine)
    store.replace_source_specs("git-main", [_spec("a"), _spec("b")])
    store.replace_source_specs("git-main", [_spec("b")])
    assert [spec.name for spec in store.list_specs("git-main")] == ["b"]

    store.save_state(_state(ReconcileStatus.DRIFTED))
    loaded = store.get_state("git-main")
    assert loaded is not None and loaded.status is ReconcileStatus.DRIFTED

    store.add_drift(_drift())
    assert len(store.list_drift(object_ref="agent-1")) == 1

    store.add_run(_run())
    assert store.get_run("rr-1") is not None
    assert [run.run_id for run in store.list_runs()] == ["rr-1"]

    store.clear()
    assert store.list_specs() == []
