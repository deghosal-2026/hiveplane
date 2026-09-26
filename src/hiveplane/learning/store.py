"""Storage for run feedback (M36-01)."""

from __future__ import annotations

import threading
from typing import Protocol

from sqlalchemy import Engine, delete, select

from hiveplane.config import Settings, get_settings
from hiveplane.learning.models import FeedbackVerdict, RunFeedback
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import RunFeedbackRow
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext, TenantScopeError


class FeedbackStore(Protocol):
    """Storage interface for run-feedback records."""

    def add_feedback(
        self, record: RunFeedback, *, ctx: TenantContext = ...
    ) -> None: ...

    def get_feedback(
        self, feedback_id: str, *, ctx: TenantContext = ...
    ) -> RunFeedback | None: ...

    def list_feedback(
        self,
        *,
        run_id: str | None = None,
        workload: str | None = None,
        verdict: FeedbackVerdict | None = None,
        ctx: TenantContext = ...,
    ) -> list[RunFeedback]: ...

    def clear(self) -> None: ...


class InMemoryFeedbackStore:
    """A process-local, thread-safe feedback store."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: dict[str, tuple[RunFeedback, str]] = {}

    def add_feedback(
        self, record: RunFeedback, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(record.tenant_id)
        with self._lock:
            self._records[record.feedback_id] = (
                record.model_copy(deep=True),
                record.tenant_id,
            )

    def get_feedback(
        self, feedback_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> RunFeedback | None:
        with self._lock:
            entry = self._records.get(feedback_id)
            if entry is None or not ctx.scopes(entry[1]):
                return None
            return entry[0].model_copy(deep=True)

    def list_feedback(
        self,
        *,
        run_id: str | None = None,
        workload: str | None = None,
        verdict: FeedbackVerdict | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[RunFeedback]:
        with self._lock:
            records = [
                record
                for record, tenant in self._records.values()
                if ctx.scopes(tenant)
                and (run_id is None or record.run_id == run_id)
                and (workload is None or record.workload_id == workload)
                and (verdict is None or record.verdict is verdict)
            ]
        records.sort(key=lambda record: (record.created_at, record.feedback_id))
        return records

    def clear(self) -> None:
        with self._lock:
            self._records.clear()


class PostgresFeedbackStore:
    """A durable feedback store backed by PostgreSQL (M36-01)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def add_feedback(
        self, record: RunFeedback, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(record.tenant_id)
        payload = record.model_dump(mode="json")
        with self._session.begin() as session:
            row = session.get(RunFeedbackRow, record.feedback_id)
            if row is None:
                session.add(
                    RunFeedbackRow(
                        feedback_id=record.feedback_id,
                        run_id=record.run_id,
                        workload_id=record.workload_id,
                        verdict=record.verdict.value,
                        created_at=record.created_at,
                        tenant_id=record.tenant_id,
                        payload=payload,
                    )
                )
            else:
                if not ctx.scopes(row.tenant_id):
                    raise TenantScopeError(
                        ctx.tenant_id, f"cannot modify feedback {record.feedback_id!r}"
                    )
                row.verdict = record.verdict.value
                row.payload = payload

    def get_feedback(
        self, feedback_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> RunFeedback | None:
        with self._session() as session:
            row = session.get(RunFeedbackRow, feedback_id)
            if row is None or not ctx.scopes(row.tenant_id):
                return None
            return RunFeedback.model_validate(row.payload)

    def list_feedback(
        self,
        *,
        run_id: str | None = None,
        workload: str | None = None,
        verdict: FeedbackVerdict | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[RunFeedback]:
        statement = select(RunFeedbackRow)
        if run_id is not None:
            statement = statement.where(RunFeedbackRow.run_id == run_id)
        if workload is not None:
            statement = statement.where(RunFeedbackRow.workload_id == workload)
        if verdict is not None:
            statement = statement.where(RunFeedbackRow.verdict == verdict.value)
        if not ctx.is_system:
            statement = statement.where(RunFeedbackRow.tenant_id == ctx.tenant_id)
        statement = statement.order_by(
            RunFeedbackRow.created_at, RunFeedbackRow.feedback_id
        )
        with self._session() as session:
            return [
                RunFeedback.model_validate(row.payload)
                for row in session.scalars(statement)
            ]

    def clear(self) -> None:
        with self._session.begin() as session:
            session.execute(delete(RunFeedbackRow))


def build_feedback_store(settings: Settings | None = None) -> FeedbackStore:
    """Build the configured feedback store (in-memory by default)."""
    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresFeedbackStore(create_engine_from_settings(resolved))
    return InMemoryFeedbackStore()
