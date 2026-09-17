"""PostgresRunStore round-trip and durability tests (Postgres-gated)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine

from hiveplane.core.event import EventType, RunEvent
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.usage import UsageReport
from hiveplane.execution.errors import RunNotFoundError
from hiveplane.execution.models import AdmissionOutcome, AdmissionResult
from hiveplane.persistence.base import create_engine_from_settings
from hiveplane.persistence.run_store import PostgresRunStore

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _run(state: RunState = RunState.PAUSED) -> Run:
    return Run(
        id="run-1",
        workload_id="agent-1",
        caller="cli",
        state=state,
        created_at=_NOW,
        updated_at=_NOW,
        context=AdmissionContext.PRODUCTION,
        sandbox=True,
    )


def test_round_trip(pg_engine: Engine) -> None:
    store = PostgresRunStore(pg_engine)
    store.clear()
    store.save_run(_run())
    store.add_event(
        RunEvent(
            run_id="run-1",
            sequence=0,
            type=EventType.STATE_CHANGE,
            actor="operator",
            timestamp=_NOW,
            from_state=RunState.RUNNING,
            to_state=RunState.PAUSED,
        )
    )
    store.add_usage(
        UsageReport(
            run_id="run-1",
            input_tokens=10,
            output_tokens=5,
            tool_calls=1,
            cost_usd=0.02,
            timestamp=_NOW,
        )
    )
    store.save_admission(
        AdmissionResult(
            run_id="run-1",
            workload="agent-1",
            context=AdmissionContext.PRODUCTION,
            outcome=AdmissionOutcome.ADMITTED,
        )
    )

    assert store.get_run("run-1") is not None
    assert store.get_run("run-1").state is RunState.PAUSED  # type: ignore[union-attr]
    assert store.list_events("run-1")[0].to_state is RunState.PAUSED
    assert store.list_usage("run-1")[0].total_tokens == 15
    admission = store.get_admission("run-1")
    assert admission is not None
    assert admission.outcome is AdmissionOutcome.ADMITTED
    assert {run.id for run in store.list_runs(workload="agent-1")} == {"run-1"}


def test_unknown_run_raises(pg_engine: Engine) -> None:
    store = PostgresRunStore(pg_engine)
    with pytest.raises(RunNotFoundError):
        store.list_events("missing")


def test_paused_run_survives_restart(pg_engine: Engine) -> None:
    store = PostgresRunStore(pg_engine)
    store.clear()
    store.save_run(_run(state=RunState.PAUSED))

    fresh_engine = create_engine_from_settings()
    try:
        reopened = PostgresRunStore(fresh_engine)
        assert reopened.get_run("run-1") is not None
        assert reopened.get_run("run-1").state is RunState.PAUSED  # type: ignore[union-attr]
    finally:
        fresh_engine.dispose()
