"""Approval request storage."""

from __future__ import annotations

import threading
from typing import Protocol

from hiveplane.core.approval import ApprovalRecord, ApprovalStatus


class ApprovalStore(Protocol):
    """Storage for approval requests."""

    def save(self, record: ApprovalRecord) -> None: ...

    def get(self, approval_id: str) -> ApprovalRecord | None: ...

    def list_approvals(
        self, *, status: ApprovalStatus | None = None, workload: str | None = None
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
        self, *, status: ApprovalStatus | None = None, workload: str | None = None
    ) -> list[ApprovalRecord]:
        """List approvals, optionally filtered."""
        with self._lock:
            records = list(self._records.values())
        if status is not None:
            records = [record for record in records if record.status is status]
        if workload is not None:
            records = [record for record in records if record.workload == workload]
        records.sort(key=lambda record: record.requested_at)
        return [record.model_copy(deep=True) for record in records]
