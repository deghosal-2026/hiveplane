"""Trigger storage: declarations, events, runs, DLQ, and replay nonces (M27).

Two implementations share one protocol: an in-memory store for tests and a
Postgres store for durability. Declarations persist the full DSL document in the
``triggers`` payload while typed columns carry the fields used for filtering.
Every read and write is filtered by the acting tenant context.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import Engine, delete, select

from hiveplane.config import Settings, get_settings
from hiveplane.fleet.triggers import (
    TriggerDlqEntry,
    TriggerEvent,
    TriggerRun,
)
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import (
    TriggerDlqRow,
    TriggerEventRow,
    TriggerNonceRow,
    TriggerRow,
    TriggerRunRow,
)
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext, TenantScopeError
from hiveplane.triggers.schema import TriggerSpec


class TriggerStore(Protocol):
    """Storage interface for triggers, their history, DLQ, and replay nonces."""

    def save_trigger(self, spec: TriggerSpec, *, ctx: TenantContext = ...) -> None: ...

    def get_trigger(
        self, trigger_id: str, *, ctx: TenantContext = ...
    ) -> TriggerSpec | None: ...

    def list_triggers(
        self, *, enabled: bool | None = None, ctx: TenantContext = ...
    ) -> list[TriggerSpec]: ...

    def delete_trigger(self, trigger_id: str, *, ctx: TenantContext = ...) -> bool: ...

    def add_event(self, event: TriggerEvent, *, ctx: TenantContext = ...) -> None: ...

    def list_events(
        self,
        trigger_id: str | None = None,
        *,
        since: datetime | None = None,
        ctx: TenantContext = ...,
    ) -> list[TriggerEvent]: ...

    def last_event(
        self, trigger_id: str, *, ctx: TenantContext = ...
    ) -> TriggerEvent | None: ...

    def find_dedup_event(
        self,
        trigger_id: str,
        dedup_key: str,
        *,
        since: datetime,
        ctx: TenantContext = ...,
    ) -> TriggerEvent | None: ...

    def add_run(self, run: TriggerRun, *, ctx: TenantContext = ...) -> None: ...

    def list_runs(
        self, trigger_id: str | None = None, *, ctx: TenantContext = ...
    ) -> list[TriggerRun]: ...

    def add_dlq(self, entry: TriggerDlqEntry, *, ctx: TenantContext = ...) -> None: ...

    def get_dlq(
        self, entry_id: str, *, ctx: TenantContext = ...
    ) -> TriggerDlqEntry | None: ...

    def list_dlq(self, *, ctx: TenantContext = ...) -> list[TriggerDlqEntry]: ...

    def mark_dlq_replayed(
        self, entry_id: str, *, replayed_at: datetime, ctx: TenantContext = ...
    ) -> None: ...

    def claim_nonce(
        self,
        trigger_id: str,
        nonce: str,
        *,
        seen_at: datetime,
        window_seconds: int,
        ctx: TenantContext = ...,
    ) -> bool: ...

    def clear(self) -> None: ...


class InMemoryTriggerStore:
    """A process-local, thread-safe trigger store."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._triggers: dict[str, tuple[TriggerSpec, str]] = {}
        self._events: dict[str, TriggerEvent] = {}
        self._runs: dict[tuple[str, str], TriggerRun] = {}
        self._dlq: dict[str, TriggerDlqEntry] = {}
        self._nonces: dict[tuple[str, str], datetime] = {}

    def save_trigger(self, spec: TriggerSpec, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(spec.tenant_id)
        with self._lock:
            self._triggers[spec.id] = (spec.model_copy(deep=True), spec.tenant_id)

    def get_trigger(
        self, trigger_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> TriggerSpec | None:
        with self._lock:
            entry = self._triggers.get(trigger_id)
            if entry is None or not ctx.scopes(entry[1]):
                return None
            return entry[0].model_copy(deep=True)

    def list_triggers(
        self, *, enabled: bool | None = None, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[TriggerSpec]:
        with self._lock:
            specs = [
                spec
                for spec, tenant in self._triggers.values()
                if ctx.scopes(tenant) and (enabled is None or spec.enabled is enabled)
            ]
            specs.sort(key=lambda spec: spec.id)
            return [spec.model_copy(deep=True) for spec in specs]

    def delete_trigger(self, trigger_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT) -> bool:
        with self._lock:
            entry = self._triggers.get(trigger_id)
            if entry is None or not ctx.scopes(entry[1]):
                return False
            del self._triggers[trigger_id]
            self._events = {
                event_id: event
                for event_id, event in self._events.items()
                if event.trigger_id != trigger_id
            }
            self._runs = {
                key: run for key, run in self._runs.items() if key[0] != trigger_id
            }
            self._dlq = {
                entry_id: item
                for entry_id, item in self._dlq.items()
                if item.trigger_id != trigger_id
            }
            return True

    def add_event(self, event: TriggerEvent, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(event.tenant_id)
        with self._lock:
            self._events[event.event_id] = event.model_copy(deep=True)

    def list_events(
        self,
        trigger_id: str | None = None,
        *,
        since: datetime | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[TriggerEvent]:
        with self._lock:
            events = [
                event
                for event in self._events.values()
                if ctx.scopes(event.tenant_id)
                and (trigger_id is None or event.trigger_id == trigger_id)
                and (since is None or event.received_at >= since)
            ]
            events.sort(key=lambda event: (event.received_at, event.event_id))
            return [event.model_copy(deep=True) for event in events]

    def last_event(
        self, trigger_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> TriggerEvent | None:
        events = self.list_events(trigger_id, ctx=ctx)
        return events[-1] if events else None

    def find_dedup_event(
        self,
        trigger_id: str,
        dedup_key: str,
        *,
        since: datetime,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> TriggerEvent | None:
        matches = [
            event
            for event in self.list_events(trigger_id, since=since, ctx=ctx)
            if event.dedup_key == dedup_key
        ]
        return matches[-1] if matches else None

    def add_run(self, run: TriggerRun, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(run.tenant_id)
        with self._lock:
            self._runs[(run.trigger_id, run.event_id)] = run.model_copy(deep=True)

    def list_runs(
        self, trigger_id: str | None = None, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[TriggerRun]:
        with self._lock:
            runs = [
                run
                for run in self._runs.values()
                if ctx.scopes(run.tenant_id)
                and (trigger_id is None or run.trigger_id == trigger_id)
            ]
            runs.sort(key=lambda run: (run.fired_at, run.trigger_id, run.event_id))
            return [run.model_copy(deep=True) for run in runs]

    def add_dlq(self, entry: TriggerDlqEntry, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(entry.tenant_id)
        with self._lock:
            self._dlq[entry.entry_id] = entry.model_copy(deep=True)

    def get_dlq(
        self, entry_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> TriggerDlqEntry | None:
        with self._lock:
            entry = self._dlq.get(entry_id)
            if entry is None or not ctx.scopes(entry.tenant_id):
                return None
            return entry.model_copy(deep=True)

    def list_dlq(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> list[TriggerDlqEntry]:
        with self._lock:
            entries = [
                entry for entry in self._dlq.values() if ctx.scopes(entry.tenant_id)
            ]
            entries.sort(key=lambda entry: (entry.created_at, entry.entry_id))
            return [entry.model_copy(deep=True) for entry in entries]

    def mark_dlq_replayed(
        self, entry_id: str, *, replayed_at: datetime, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        with self._lock:
            entry = self._dlq.get(entry_id)
            if entry is None or not ctx.scopes(entry.tenant_id):
                raise TenantScopeError(ctx.tenant_id, f"cannot access DLQ {entry_id!r}")
            self._dlq[entry_id] = entry.model_copy(update={"replayed_at": replayed_at})

    def claim_nonce(
        self,
        trigger_id: str,
        nonce: str,
        *,
        seen_at: datetime,
        window_seconds: int,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> bool:
        cutoff = seen_at - timedelta(seconds=window_seconds)
        with self._lock:
            existing = self._nonces.get((trigger_id, nonce))
            if existing is not None and existing >= cutoff:
                return False
            self._nonces[(trigger_id, nonce)] = seen_at
            return True

    def clear(self) -> None:
        with self._lock:
            self._triggers.clear()
            self._events.clear()
            self._runs.clear()
            self._dlq.clear()
            self._nonces.clear()


class PostgresTriggerStore:
    """A durable trigger store backed by PostgreSQL (M27)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def save_trigger(self, spec: TriggerSpec, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(spec.tenant_id)
        with self._session.begin() as session:
            row = session.get(TriggerRow, spec.id)
            created_at = spec.created_at or datetime.now(UTC)
            if row is None:
                session.add(
                    TriggerRow(
                        trigger_id=spec.id,
                        tenant_id=spec.tenant_id,
                        source=spec.source.value,
                        target_kind=spec.target.kind.value,
                        target_ref=spec.target.ref,
                        cooldown_seconds=spec.cooldown_seconds,
                        admission_rule=spec.admission_rule.value,
                        enabled=spec.enabled,
                        created_at=created_at,
                        payload=spec.model_dump(mode="json"),
                    )
                )
            else:
                if not ctx.scopes(row.tenant_id):
                    raise TenantScopeError(
                        ctx.tenant_id, f"cannot modify trigger {spec.id!r}"
                    )
                row.source = spec.source.value
                row.target_kind = spec.target.kind.value
                row.target_ref = spec.target.ref
                row.cooldown_seconds = spec.cooldown_seconds
                row.admission_rule = spec.admission_rule.value
                row.enabled = spec.enabled
                row.payload = spec.model_dump(mode="json")

    def get_trigger(
        self, trigger_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> TriggerSpec | None:
        with self._session() as session:
            row = session.get(TriggerRow, trigger_id)
            if row is None or not ctx.scopes(row.tenant_id):
                return None
            return TriggerSpec.model_validate(row.payload)

    def list_triggers(
        self, *, enabled: bool | None = None, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[TriggerSpec]:
        statement = select(TriggerRow).order_by(TriggerRow.trigger_id)
        if enabled is not None:
            statement = statement.where(TriggerRow.enabled.is_(enabled))
        if not ctx.is_system:
            statement = statement.where(TriggerRow.tenant_id == ctx.tenant_id)
        with self._session() as session:
            return [TriggerSpec.model_validate(row.payload) for row in session.scalars(statement)]

    def delete_trigger(self, trigger_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT) -> bool:
        with self._session.begin() as session:
            row = session.get(TriggerRow, trigger_id)
            if row is None or not ctx.scopes(row.tenant_id):
                return False
            session.execute(delete(TriggerRunRow).where(TriggerRunRow.trigger_id == trigger_id))
            session.execute(
                delete(TriggerEventRow).where(TriggerEventRow.trigger_id == trigger_id)
            )
            session.execute(delete(TriggerDlqRow).where(TriggerDlqRow.trigger_id == trigger_id))
            session.delete(row)
            return True

    def add_event(self, event: TriggerEvent, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(event.tenant_id)
        with self._session.begin() as session:
            session.add(
                TriggerEventRow(
                    event_id=event.event_id,
                    trigger_id=event.trigger_id,
                    tenant_id=event.tenant_id,
                    source=event.source.value,
                    received_at=event.received_at,
                    dedup_key=event.dedup_key,
                    outcome=event.outcome.value,
                    payload=event.model_dump(mode="json"),
                )
            )

    def list_events(
        self,
        trigger_id: str | None = None,
        *,
        since: datetime | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[TriggerEvent]:
        statement = select(TriggerEventRow).order_by(
            TriggerEventRow.received_at, TriggerEventRow.event_id
        )
        if trigger_id is not None:
            statement = statement.where(TriggerEventRow.trigger_id == trigger_id)
        if since is not None:
            statement = statement.where(TriggerEventRow.received_at >= since)
        if not ctx.is_system:
            statement = statement.where(TriggerEventRow.tenant_id == ctx.tenant_id)
        with self._session() as session:
            return [TriggerEvent.model_validate(row.payload) for row in session.scalars(statement)]

    def last_event(
        self, trigger_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> TriggerEvent | None:
        events = self.list_events(trigger_id, ctx=ctx)
        return events[-1] if events else None

    def find_dedup_event(
        self,
        trigger_id: str,
        dedup_key: str,
        *,
        since: datetime,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> TriggerEvent | None:
        matches = [
            event
            for event in self.list_events(trigger_id, since=since, ctx=ctx)
            if event.dedup_key == dedup_key
        ]
        return matches[-1] if matches else None

    def add_run(self, run: TriggerRun, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(run.tenant_id)
        with self._session.begin() as session:
            row = session.get(TriggerRunRow, (run.trigger_id, run.event_id))
            if row is None:
                session.add(
                    TriggerRunRow(
                        trigger_id=run.trigger_id,
                        event_id=run.event_id,
                        tenant_id=run.tenant_id,
                        run_id=run.run_id,
                        fired_at=run.fired_at,
                        status=run.status.value,
                        reason=run.reason,
                        payload=run.model_dump(mode="json"),
                    )
                )
            else:
                row.run_id = run.run_id
                row.status = run.status.value
                row.reason = run.reason
                row.payload = run.model_dump(mode="json")

    def list_runs(
        self, trigger_id: str | None = None, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[TriggerRun]:
        statement = select(TriggerRunRow).order_by(TriggerRunRow.fired_at, TriggerRunRow.event_id)
        if trigger_id is not None:
            statement = statement.where(TriggerRunRow.trigger_id == trigger_id)
        if not ctx.is_system:
            statement = statement.where(TriggerRunRow.tenant_id == ctx.tenant_id)
        with self._session() as session:
            return [TriggerRun.model_validate(row.payload) for row in session.scalars(statement)]

    def add_dlq(self, entry: TriggerDlqEntry, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(entry.tenant_id)
        with self._session.begin() as session:
            session.add(
                TriggerDlqRow(
                    entry_id=entry.entry_id,
                    trigger_id=entry.trigger_id,
                    event_id=entry.event_id,
                    tenant_id=entry.tenant_id,
                    failure_reason=entry.failure_reason,
                    attempts=entry.attempts,
                    created_at=entry.created_at,
                    replayed_at=entry.replayed_at,
                    payload=entry.model_dump(mode="json"),
                )
            )

    def get_dlq(
        self, entry_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> TriggerDlqEntry | None:
        with self._session() as session:
            row = session.get(TriggerDlqRow, entry_id)
            if row is None or not ctx.scopes(row.tenant_id):
                return None
            return TriggerDlqEntry.model_validate(row.payload)

    def list_dlq(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> list[TriggerDlqEntry]:
        statement = select(TriggerDlqRow).order_by(TriggerDlqRow.created_at, TriggerDlqRow.entry_id)
        if not ctx.is_system:
            statement = statement.where(TriggerDlqRow.tenant_id == ctx.tenant_id)
        with self._session() as session:
            return [
                TriggerDlqEntry.model_validate(row.payload)
                for row in session.scalars(statement)
            ]

    def mark_dlq_replayed(
        self, entry_id: str, *, replayed_at: datetime, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        with self._session.begin() as session:
            row = session.get(TriggerDlqRow, entry_id)
            if row is None or not ctx.scopes(row.tenant_id):
                raise TenantScopeError(ctx.tenant_id, f"cannot access DLQ {entry_id!r}")
            row.replayed_at = replayed_at
            payload = dict(row.payload)
            payload["replayed_at"] = replayed_at.isoformat()
            row.payload = payload

    def claim_nonce(
        self,
        trigger_id: str,
        nonce: str,
        *,
        seen_at: datetime,
        window_seconds: int,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> bool:
        cutoff = seen_at - timedelta(seconds=window_seconds)
        with self._session.begin() as session:
            row = session.get(TriggerNonceRow, (trigger_id, nonce))
            if row is not None and row.seen_at >= cutoff:
                return False
            if row is None:
                session.add(
                    TriggerNonceRow(
                        trigger_id=trigger_id,
                        nonce=nonce,
                        tenant_id=ctx.tenant_id,
                        seen_at=seen_at,
                        payload={},
                    )
                )
            else:
                row.seen_at = seen_at
            return True

    def clear(self) -> None:
        with self._session.begin() as session:
            for table in (
                TriggerNonceRow,
                TriggerDlqRow,
                TriggerRunRow,
                TriggerEventRow,
                TriggerRow,
            ):
                session.execute(delete(table))


def build_trigger_store(settings: Settings | None = None) -> TriggerStore:
    """Build the configured trigger store (in-memory by default)."""
    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresTriggerStore(create_engine_from_settings(resolved))
    return InMemoryTriggerStore()
