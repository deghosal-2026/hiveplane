"""Certification record storage (M7, #25)."""

from __future__ import annotations

from typing import Protocol

from hiveplane.certification.models import CertificationRecord, CertificationStatus


class CertificationStore(Protocol):
    """Append-only storage for certification records."""

    def add(self, record: CertificationRecord) -> None: ...

    def get(self, certification_id: str) -> CertificationRecord | None: ...

    def list(
        self,
        *,
        workload: str | None = None,
        status: CertificationStatus | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[CertificationRecord]: ...


class InMemoryCertificationStore:
    """In-memory, insertion-ordered certification store."""

    def __init__(self) -> None:
        self._records: dict[str, CertificationRecord] = {}

    def add(self, record: CertificationRecord) -> None:
        """Store a certification record, keyed by certification id."""
        self._records[record.certification.certification_id] = record.model_copy(deep=True)

    def get(self, certification_id: str) -> CertificationRecord | None:
        """Return a certification record by id, or ``None``."""
        record = self._records.get(certification_id)
        return record.model_copy(deep=True) if record is not None else None

    def list(
        self,
        *,
        workload: str | None = None,
        status: CertificationStatus | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[CertificationRecord]:
        """Return records filtered by workload and status, oldest first."""
        records = [
            record
            for record in self._records.values()
            if (workload is None or record.certification.workload_id == workload)
            and (status is None or record.certification.status is status)
        ]
        records.sort(key=lambda record: record.certification.timestamp)
        if offset:
            records = records[offset:]
        if limit is not None:
            records = records[:limit]
        return [record.model_copy(deep=True) for record in records]
