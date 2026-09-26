"""Append-only security events for injection, egress, taint, and escalation (M39-06).

Events are the auditable, queryable record of every boundary block. They are
tenant-scoped, immutable, and surfaced by ``GET /security/events`` and in the
run story.
"""

from __future__ import annotations

import threading
from datetime import datetime
from enum import StrEnum
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, delete, select

from hiveplane.config import Settings, get_settings
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import SecurityEventRow
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext


class SecurityEventKind(StrEnum):
    """The class of boundary event recorded."""

    INJECTION = "injection"
    EGRESS_DENIED = "egress_denied"
    TAINT_BLOCK = "taint_block"
    REPEATED_ATTEMPT = "repeated_attempt"


class SecurityEvent(BaseModel):
    """A single immutable defense telemetry record."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    run_id: str | None = None
    workload_id: str | None = None
    kind: SecurityEventKind
    detector_id: str | None = None
    detector_version: str | None = None
    detail: dict[str, object] = Field(default_factory=dict)
    created_at: datetime
    tenant_id: str = DEFAULT_CONTEXT.tenant_id


class SecurityEventStore(Protocol):
    """Storage for security events, scoped by the acting tenant context."""

    def add(self, event: SecurityEvent, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None: ...

    def list_events(
        self,
        *,
        workload: str | None = None,
        run_id: str | None = None,
        kind: SecurityEventKind | None = None,
        since: datetime | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[SecurityEvent]: ...


class InMemorySecurityEventStore:
    """A process-local, thread-safe security-event store."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._events: list[tuple[SecurityEvent, str]] = []

    def add(self, event: SecurityEvent, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        """Append an event."""
        ctx.require(event.tenant_id)
        with self._lock:
            self._events.append((event.model_copy(deep=True), event.tenant_id))

    def list_events(
        self,
        *,
        workload: str | None = None,
        run_id: str | None = None,
        kind: SecurityEventKind | None = None,
        since: datetime | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[SecurityEvent]:
        """List events, oldest first, filtered."""
        with self._lock:
            events = [
                event
                for event, tenant_id in self._events
                if ctx.scopes(tenant_id)
                and (workload is None or event.workload_id == workload)
                and (run_id is None or event.run_id == run_id)
                and (kind is None or event.kind is kind)
                and (since is None or event.created_at >= since)
            ]
        events.sort(key=lambda event: (event.created_at, event.event_id))
        return [event.model_copy(deep=True) for event in events]

    def clear(self) -> None:
        """Drop all events; used by tests and destructive operations."""
        with self._lock:
            self._events.clear()


class PostgresSecurityEventStore:
    """A durable security-event store backed by PostgreSQL (M39-06)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def add(self, event: SecurityEvent, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        """Append an event."""
        ctx.require(event.tenant_id)
        with self._session.begin() as session:
            session.add(
                SecurityEventRow(
                    event_id=event.event_id,
                    run_id=event.run_id,
                    workload_id=event.workload_id,
                    kind=event.kind.value,
                    created_at=event.created_at,
                    tenant_id=event.tenant_id,
                    payload=event.model_dump(mode="json"),
                )
            )

    def list_events(
        self,
        *,
        workload: str | None = None,
        run_id: str | None = None,
        kind: SecurityEventKind | None = None,
        since: datetime | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[SecurityEvent]:
        """List events, oldest first, filtered."""
        statement = select(SecurityEventRow)
        if workload is not None:
            statement = statement.where(SecurityEventRow.workload_id == workload)
        if run_id is not None:
            statement = statement.where(SecurityEventRow.run_id == run_id)
        if kind is not None:
            statement = statement.where(SecurityEventRow.kind == kind.value)
        if since is not None:
            statement = statement.where(SecurityEventRow.created_at >= since)
        if not ctx.is_system:
            statement = statement.where(SecurityEventRow.tenant_id == ctx.tenant_id)
        statement = statement.order_by(
            SecurityEventRow.created_at, SecurityEventRow.event_id
        )
        with self._session() as session:
            return [
                SecurityEvent.model_validate(row.payload) for row in session.scalars(statement)
            ]

    def clear(self) -> None:
        """Delete all events; used by tests and destructive operations."""
        with self._session.begin() as session:
            session.execute(delete(SecurityEventRow))


def build_security_event_store(settings: Settings | None = None) -> SecurityEventStore:
    """Build the configured security-event store (in-memory by default)."""
    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresSecurityEventStore(create_engine_from_settings(resolved))
    return InMemorySecurityEventStore()
