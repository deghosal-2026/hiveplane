"""Storage for replay operations — replay, fork, and A/B records (M60)."""

from __future__ import annotations

import threading
from typing import Protocol

from sqlalchemy import Engine, delete, select

from hiveplane.config import Settings, get_settings
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import ReplayRow
from hiveplane.replay.models import ReplayMode, ReplayRecord
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext, TenantScopeError


class ReplayStore(Protocol):
    """Storage interface for replay records."""

    def add_replay(
        self, record: ReplayRecord, *, ctx: TenantContext = ...
    ) -> None: ...

    def get_replay(
        self, replay_id: str, *, ctx: TenantContext = ...
    ) -> ReplayRecord | None: ...

    def list_replays(
        self,
        *,
        source_run_id: str | None = None,
        mode: ReplayMode | None = None,
        ctx: TenantContext = ...,
    ) -> list[ReplayRecord]: ...

    def clear(self) -> None: ...


class InMemoryReplayStore:
    """A process-local, thread-safe replay store."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: dict[str, tuple[ReplayRecord, str]] = {}

    def add_replay(
        self, record: ReplayRecord, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(record.tenant_id)
        with self._lock:
            self._records[record.replay_id] = (record.model_copy(deep=True), record.tenant_id)

    def get_replay(
        self, replay_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> ReplayRecord | None:
        with self._lock:
            entry = self._records.get(replay_id)
            if entry is None or not ctx.scopes(entry[1]):
                return None
            return entry[0].model_copy(deep=True)

    def list_replays(
        self,
        *,
        source_run_id: str | None = None,
        mode: ReplayMode | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[ReplayRecord]:
        with self._lock:
            records = [
                record
                for record, tenant in self._records.values()
                if ctx.scopes(tenant)
                and (source_run_id is None or record.source_run_id == source_run_id)
                and (mode is None or record.mode == mode)
            ]
        records.sort(key=lambda record: (record.created_at, record.replay_id))
        return records

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


class PostgresReplayStore:
    """A durable replay store backed by PostgreSQL (M60)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def add_replay(
        self, record: ReplayRecord, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(record.tenant_id)
        payload = record.model_dump(mode="json")
        with self._session.begin() as session:
            row = session.get(ReplayRow, record.replay_id)
            if row is None:
                session.add(
                    ReplayRow(
                        replay_id=record.replay_id,
                        source_run_id=record.source_run_id,
                        mode=record.mode.value,
                        created_at=record.created_at,
                        tenant_id=record.tenant_id,
                        payload=payload,
                    )
                )
            else:
                if not ctx.scopes(row.tenant_id):
                    raise TenantScopeError(
                        ctx.tenant_id, f"cannot modify replay {record.replay_id!r}"
                    )
                row.mode = record.mode.value
                row.payload = payload

    def get_replay(
        self, replay_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> ReplayRecord | None:
        with self._session() as session:
            row = session.get(ReplayRow, replay_id)
            if row is None or not ctx.scopes(row.tenant_id):
                return None
            return ReplayRecord.model_validate(row.payload)

    def list_replays(
        self,
        *,
        source_run_id: str | None = None,
        mode: ReplayMode | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[ReplayRecord]:
        statement = select(ReplayRow)
        if source_run_id is not None:
            statement = statement.where(ReplayRow.source_run_id == source_run_id)
        if mode is not None:
            statement = statement.where(ReplayRow.mode == mode.value)
        if not ctx.is_system:
            statement = statement.where(ReplayRow.tenant_id == ctx.tenant_id)
        statement = statement.order_by(ReplayRow.created_at, ReplayRow.replay_id)
        with self._session() as session:
            return [
                ReplayRecord.model_validate(row.payload) for row in session.scalars(statement)
            ]

    def clear(self) -> None:
        with self._session.begin() as session:
            session.execute(delete(ReplayRow))


def build_replay_store(settings: Settings | None = None) -> ReplayStore:
    """Build the configured replay store (in-memory by default)."""
    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresReplayStore(create_engine_from_settings(resolved))
    return InMemoryReplayStore()
