"""Approval request storage."""

from __future__ import annotations

import threading
from typing import Protocol

from sqlalchemy import Engine, delete, select

from hiveplane.config import Settings, get_settings
from hiveplane.core.approval import ApprovalRecord, ApprovalStatus
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import ApprovalRow


class ApprovalStore(Protocol):
    """Storage for approval requests."""

    def save(self, record: ApprovalRecord) -> None: ...

    def get(self, approval_id: str) -> ApprovalRecord | None: ...

    def list_approvals(
        self,
        *,
        status: ApprovalStatus | None = None,
        workload: str | None = None,
        run_id: str | None = None,
    ) -> list[ApprovalRecord]: ...


class InMemoryApprovalStore:
    """A process-local, thread-safe approval store."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._records: dict[str, ApprovalRecord] = {}

    def save(self, record: ApprovalRecord) -> None:
        """Persist an approval record."""
        with self._lock:
            self._records[record.approval_id] = record.model_copy(deep=True)

    def get(self, approval_id: str) -> ApprovalRecord | None:
        """Return an approval by id."""
        with self._lock:
            record = self._records.get(approval_id)
            return record.model_copy(deep=True) if record is not None else None

    def list_approvals(
        self,
        *,
        status: ApprovalStatus | None = None,
        workload: str | None = None,
        run_id: str | None = None,
    ) -> list[ApprovalRecord]:
        """List approvals, optionally filtered."""
        with self._lock:
            records = list(self._records.values())
        if status is not None:
            records = [record for record in records if record.status is status]
        if workload is not None:
            records = [record for record in records if record.workload == workload]
        if run_id is not None:
            records = [record for record in records if record.run_id == run_id]
        records.sort(key=lambda record: record.requested_at)
        return [record.model_copy(deep=True) for record in records]


class PostgresApprovalStore:
    """A durable approval store backed by PostgreSQL (#118)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def save(self, record: ApprovalRecord) -> None:
        """Insert or update an approval record."""
        with self._session.begin() as session:
            row = session.get(ApprovalRow, record.approval_id)
            if row is None:
                session.add(
                    ApprovalRow(
                        approval_id=record.approval_id,
                        run_id=record.run_id,
                        status=record.status.value,
                        created_at=record.requested_at,
                        payload=record.model_dump(mode="json"),
                    )
                )
            else:
                row.run_id = record.run_id
                row.status = record.status.value
                row.created_at = record.requested_at
                row.payload = record.model_dump(mode="json")

    def get(self, approval_id: str) -> ApprovalRecord | None:
        """Return an approval by id, or ``None``."""
        with self._session() as session:
            row = session.get(ApprovalRow, approval_id)
            return ApprovalRecord.model_validate(row.payload) if row is not None else None

    def list_approvals(
        self,
        *,
        status: ApprovalStatus | None = None,
        workload: str | None = None,
        run_id: str | None = None,
    ) -> list[ApprovalRecord]:
        """List approvals, optionally filtered, oldest first."""
        statement = select(ApprovalRow).order_by(ApprovalRow.created_at)
        if status is not None:
            statement = statement.where(ApprovalRow.status == status.value)
        if run_id is not None:
            statement = statement.where(ApprovalRow.run_id == run_id)
        with self._session() as session:
            records = [
                ApprovalRecord.model_validate(row.payload)
                for row in session.scalars(statement)
            ]
        if workload is not None:
            records = [record for record in records if record.workload == workload]
        return records

    def clear(self) -> None:
        """Delete all approval data; used by tests and destructive operations."""
        with self._session.begin() as session:
            session.execute(delete(ApprovalRow))


def build_approval_store(settings: Settings | None = None) -> ApprovalStore:
    """Build the configured approval store (in-memory by default)."""
    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresApprovalStore(create_engine_from_settings(resolved))
    return InMemoryApprovalStore()
