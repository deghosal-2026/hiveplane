"""Promotion-record storage (M32-03).

Every promotion attempt — admitted or refused — is persisted for attribution and
audit.
"""

from __future__ import annotations

import threading
from typing import Protocol

from sqlalchemy import Engine, delete, select

from hiveplane.certification.promotion import PromotionRecord
from hiveplane.config import Settings, get_settings
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import PromotionRow
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext, TenantScopeError


class PromotionStore(Protocol):
    """Storage interface for promotion records."""

    def save_record(
        self, record: PromotionRecord, *, ctx: TenantContext = ...
    ) -> None: ...

    def get_record(
        self, promotion_id: str, *, ctx: TenantContext = ...
    ) -> PromotionRecord | None: ...

    def list_records(self, *, ctx: TenantContext = ...) -> list[PromotionRecord]: ...

    def clear(self) -> None: ...


class InMemoryPromotionStore:
    """A process-local, thread-safe promotion store."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: dict[str, tuple[PromotionRecord, str]] = {}

    def save_record(
        self, record: PromotionRecord, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(record.tenant_id)
        with self._lock:
            self._records[record.promotion_id] = (record.model_copy(deep=True), record.tenant_id)

    def get_record(
        self, promotion_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> PromotionRecord | None:
        with self._lock:
            entry = self._records.get(promotion_id)
            if entry is None or not ctx.scopes(entry[1]):
                return None
            return entry[0].model_copy(deep=True)

    def list_records(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> list[PromotionRecord]:
        with self._lock:
            records = [
                record for record, tenant in self._records.values() if ctx.scopes(tenant)
            ]
            records.sort(key=lambda record: (record.timestamp, record.promotion_id))
            return [record.model_copy(deep=True) for record in records]

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


class PostgresPromotionStore:
    """A durable promotion store backed by PostgreSQL (M32-03)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def save_record(
        self, record: PromotionRecord, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(record.tenant_id)
        payload = record.model_dump(mode="json")
        with self._session.begin() as session:
            row = session.get(PromotionRow, record.promotion_id)
            if row is None:
                session.add(
                    PromotionRow(
                        promotion_id=record.promotion_id,
                        tenant_id=record.tenant_id,
                        workload=record.workload,
                        manifest_version=record.manifest_version,
                        status=record.status,
                        to_context=record.to_context.value,
                        certification_id=record.certification_id,
                        created_at=record.timestamp,
                        payload=payload,
                    )
                )
            else:
                if not ctx.scopes(row.tenant_id):
                    raise TenantScopeError(
                        ctx.tenant_id, f"cannot modify promotion {record.promotion_id!r}"
                    )
                row.status = record.status
                row.certification_id = record.certification_id
                row.payload = payload

    def get_record(
        self, promotion_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> PromotionRecord | None:
        with self._session() as session:
            row = session.get(PromotionRow, promotion_id)
            if row is None or not ctx.scopes(row.tenant_id):
                return None
            return PromotionRecord.model_validate(row.payload)

    def list_records(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> list[PromotionRecord]:
        statement = select(PromotionRow).order_by(
            PromotionRow.created_at, PromotionRow.promotion_id
        )
        if not ctx.is_system:
            statement = statement.where(PromotionRow.tenant_id == ctx.tenant_id)
        with self._session() as session:
            return [
                PromotionRecord.model_validate(row.payload)
                for row in session.scalars(statement)
            ]

    def clear(self) -> None:
        with self._session.begin() as session:
            session.execute(delete(PromotionRow))


def build_promotion_store(settings: Settings | None = None) -> PromotionStore:
    """Build the configured promotion store (in-memory by default)."""
    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresPromotionStore(create_engine_from_settings(resolved))
    return InMemoryPromotionStore()
