"""Trigger store tests: in-memory and Postgres (M27)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine

from hiveplane.fleet.triggers import (
    TriggerDlqEntry,
    TriggerEvent,
    TriggerOutcome,
    TriggerRun,
    TriggerRunStatus,
    TriggerSource,
)
from hiveplane.triggers.schema import TriggerSpec
from hiveplane.triggers.store import (
    InMemoryTriggerStore,
    PostgresTriggerStore,
    TriggerStore,
)
from postgres import reset_database

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _spec(trigger_id: str = "pr-analysis", enabled: bool = True) -> TriggerSpec:
    return TriggerSpec.model_validate(
        {
            "id": trigger_id,
            "source": "github",
            "target": {"kind": "workload", "ref": "agent-1"},
            "filter": {"event": ["pull_request"]},
            "task_template": {"pr": "{{ event.number }}"},
            "enabled": enabled,
        }
    )


def _event(event_id: str = "ev-1", dedup_key: str | None = "k") -> TriggerEvent:
    return TriggerEvent(
        event_id=event_id,
        trigger_id="pr-analysis",
        source=TriggerSource.GITHUB,
        payload={"number": 1},
        received_at=_NOW,
        dedup_key=dedup_key,
        outcome=TriggerOutcome.ACCEPTED,
    )


def _run(run_id: str = "run-1") -> TriggerRun:
    return TriggerRun(
        trigger_id="pr-analysis",
        event_id="ev-1",
        run_id=run_id,
        fired_at=_NOW,
        status=TriggerRunStatus.SUBMITTED,
    )


def _dlq(entry_id: str = "dlq-1") -> TriggerDlqEntry:
    return TriggerDlqEntry(
        entry_id=entry_id,
        trigger_id="pr-analysis",
        event_id="ev-1",
        payload={"number": 1},
        headers={"X-HivePlane-Nonce": "n1"},
        failure_reason="execution unavailable",
        attempts=3,
        created_at=_NOW,
    )


def _assert_store(store: TriggerStore) -> None:
    store.save_trigger(_spec())
    loaded = store.get_trigger("pr-analysis")
    assert loaded is not None and loaded.source is TriggerSource.GITHUB
    assert store.get_trigger("missing") is None
    assert [s.id for s in store.list_triggers()] == ["pr-analysis"]
    assert [s.id for s in store.list_triggers(enabled=False)] == []

    store.add_event(_event())
    last = store.last_event("pr-analysis")
    assert last is not None and last.event_id == "ev-1"
    assert store.find_dedup_event("pr-analysis", "k", since=_NOW - timedelta(hours=1)) is not None
    assert store.find_dedup_event("pr-analysis", "other", since=_NOW) is None

    store.add_run(_run())
    assert [r.run_id for r in store.list_runs("pr-analysis")] == ["run-1"]

    store.add_dlq(_dlq())
    assert store.get_dlq("dlq-1") is not None
    assert [e.entry_id for e in store.list_dlq()] == ["dlq-1"]
    store.mark_dlq_replayed("dlq-1", replayed_at=_NOW)
    replayed = store.get_dlq("dlq-1")
    assert replayed is not None and replayed.replayed_at is not None

    assert store.claim_nonce("pr-analysis", "n1", seen_at=_NOW, window_seconds=300) is True
    assert store.claim_nonce("pr-analysis", "n1", seen_at=_NOW, window_seconds=300) is False
    assert (
        store.claim_nonce(
            "pr-analysis", "n1", seen_at=_NOW + timedelta(seconds=400), window_seconds=300
        )
        is True
    )

    assert store.delete_trigger("pr-analysis") is True
    assert store.delete_trigger("pr-analysis") is False


def test_in_memory_trigger_store() -> None:
    _assert_store(InMemoryTriggerStore())


def test_postgres_trigger_store(pg_engine: Engine) -> None:
    reset_database(pg_engine)
    _assert_store(PostgresTriggerStore(pg_engine))
