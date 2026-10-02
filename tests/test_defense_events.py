"""Security-event store: append-only defense telemetry (M39-06)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import Engine

from hiveplane.defense.events import (
    InMemorySecurityEventStore,
    PostgresSecurityEventStore,
    SecurityEvent,
    SecurityEventKind,
)
from hiveplane.tenancy import DEFAULT_CONTEXT, SYSTEM_CONTEXT, TenantContext
from postgres import reset_database


def _event(
    event_id: str,
    *,
    kind: SecurityEventKind = SecurityEventKind.INJECTION,
    workload: str = "repo-agent",
    run_id: str = "run-1",
    created_at: datetime | None = None,
    tenant_id: str = DEFAULT_CONTEXT.tenant_id,
) -> SecurityEvent:
    return SecurityEvent(
        event_id=event_id,
        run_id=run_id,
        workload_id=workload,
        kind=kind,
        detector_id="injection.instruction_override",
        detector_version="1.0.0",
        detail={"span": [0, 10]},
        created_at=created_at or datetime(2026, 1, 1, tzinfo=UTC),
        tenant_id=tenant_id,
    )


def test_store_round_trips_event() -> None:
    store = InMemorySecurityEventStore()
    store.add(_event("e1"))
    assert store.list_events()[0].event_id == "e1"


def test_store_filters_by_workload_and_kind() -> None:
    store = InMemorySecurityEventStore()
    store.add(_event("e1", workload="repo-agent"))
    store.add(_event("e2", workload="triage", kind=SecurityEventKind.EGRESS_DENIED))
    assert [e.event_id for e in store.list_events(workload="repo-agent")] == ["e1"]
    assert [e.event_id for e in store.list_events(kind=SecurityEventKind.EGRESS_DENIED)] == ["e2"]


def test_store_filters_by_run_and_since() -> None:
    store = InMemorySecurityEventStore()
    older = datetime(2026, 1, 1, tzinfo=UTC)
    newer = older + timedelta(hours=2)
    store.add(_event("e1", run_id="run-1", created_at=older))
    store.add(_event("e2", run_id="run-2", created_at=newer))
    assert [e.event_id for e in store.list_events(run_id="run-2")] == ["e2"]
    assert [e.event_id for e in store.list_events(since=newer)] == ["e2"]


def test_store_orders_newest_last() -> None:
    store = InMemorySecurityEventStore()
    older = datetime(2026, 1, 1, tzinfo=UTC)
    newer = older + timedelta(minutes=5)
    store.add(_event("e1", created_at=newer))
    store.add(_event("e2", created_at=older))
    assert [e.event_id for e in store.list_events()] == ["e2", "e1"]


def test_store_is_tenant_scoped() -> None:
    store = InMemorySecurityEventStore()
    tenant_a = TenantContext(tenant_id="tenant-a")
    store.add(_event("e1", tenant_id="tenant-a"), ctx=tenant_a)
    assert store.list_events(ctx=TenantContext(tenant_id="tenant-b")) == []
    assert [e.event_id for e in store.list_events(ctx=tenant_a)] == ["e1"]


def test_memory_store_clear() -> None:
    store = InMemorySecurityEventStore()
    store.add(_event("e1"))
    store.clear()
    assert store.list_events() == []


def test_postgres_store_round_trip(pg_engine: Engine) -> None:
    reset_database(pg_engine)
    store = PostgresSecurityEventStore(pg_engine)
    store.add(_event("e1"))
    store.add(_event("e2", kind=SecurityEventKind.TAINT_BLOCK))
    events = store.list_events()
    assert [e.event_id for e in events] == ["e1", "e2"]
    assert store.list_events(kind=SecurityEventKind.TAINT_BLOCK)[0].event_id == "e2"


def test_postgres_store_filters_and_clear(pg_engine: Engine) -> None:
    reset_database(pg_engine)
    store = PostgresSecurityEventStore(pg_engine)
    older = datetime(2026, 1, 1, tzinfo=UTC)
    newer = older + timedelta(hours=1)
    store.add(_event("e1", run_id="run-1", created_at=older))
    store.add(_event("e2", run_id="run-2", created_at=newer))

    assert [e.event_id for e in store.list_events(run_id="run-2")] == ["e2"]
    assert [e.event_id for e in store.list_events(since=newer)] == ["e2"]
    assert [e.event_id for e in store.list_events(workload="repo-agent")] == ["e1", "e2"]

    store.clear()
    assert store.list_events() == []


def test_postgres_store_is_tenant_scoped(pg_engine: Engine) -> None:
    reset_database(pg_engine)
    store = PostgresSecurityEventStore(pg_engine)
    tenant_a = TenantContext(tenant_id="tenant-a")
    store.add(_event("e1", tenant_id="tenant-a"), ctx=tenant_a)

    assert store.list_events(ctx=TenantContext(tenant_id="tenant-b")) == []
    assert [e.event_id for e in store.list_events(ctx=tenant_a)] == ["e1"]
    assert [e.event_id for e in store.list_events(ctx=SYSTEM_CONTEXT)] == ["e1"]
