"""Certification record storage (M7, #25)."""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import Engine, delete, select

from hiveplane.certification.models import CertificationRecord, CertificationStatus
from hiveplane.config import Settings, get_settings
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import AttestationRow, CertificationRow


class CertificationStore(Protocol):
    """Append-only storage for certification records."""

    def add(self, record: CertificationRecord) -> None: ...

    def get(self, record_id: str) -> CertificationRecord | None: ...

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
        """Store a certification record, keyed by its unique record id."""
        self._records[record.record_id] = record.model_copy(deep=True)

    def get(self, record_id: str) -> CertificationRecord | None:
        """Return a certification record by id, or ``None``."""
        record = self._records.get(record_id)
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


class PostgresCertificationStore:
    """A durable certification store backed by PostgreSQL (#126).

    The full :class:`CertificationRecord` lives in ``certifications.payload``;
    the immutable attestation is also written to ``attestations`` so attestation
    history survives independently of the certification record.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def add(self, record: CertificationRecord) -> None:
        """Insert or replace a certification record."""
        certification = record.certification
        attestation = record.attestation
        with self._session.begin() as session:
            row = session.get(CertificationRow, record.record_id)
            if row is None:
                session.add(
                    CertificationRow(
                        certification_id=record.record_id,
                        workload=certification.workload_id,
                        status=certification.status.value,
                        created_at=certification.timestamp,
                        payload=record.model_dump(mode="json"),
                    )
                )
            else:
                row.workload = certification.workload_id
                row.status = certification.status.value
                row.created_at = certification.timestamp
                row.payload = record.model_dump(mode="json")
            if session.get(AttestationRow, attestation.attestation_id) is None:
                session.add(
                    AttestationRow(
                        attestation_id=attestation.attestation_id,
                        workload=attestation.workload_id,
                        model_identity=attestation.model_identity,
                        created_at=attestation.timestamp,
                        payload=attestation.model_dump(mode="json"),
                    )
                )

    def get(self, record_id: str) -> CertificationRecord | None:
        """Return a certification record by id, or ``None``."""
        with self._session() as session:
            row = session.get(CertificationRow, record_id)
            return CertificationRecord.model_validate(row.payload) if row is not None else None

    def list(
        self,
        *,
        workload: str | None = None,
        status: CertificationStatus | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[CertificationRecord]:
        """Return records filtered by workload and status, oldest first."""
        statement = select(CertificationRow).order_by(CertificationRow.created_at)
        if workload is not None:
            statement = statement.where(CertificationRow.workload == workload)
        if status is not None:
            statement = statement.where(CertificationRow.status == status.value)
        if offset:
            statement = statement.offset(offset)
        if limit is not None:
            statement = statement.limit(limit)
        with self._session() as session:
            return [
                CertificationRecord.model_validate(row.payload)
                for row in session.scalars(statement)
            ]

    def clear(self) -> None:
        """Delete all certification data; used by tests and destructive operations."""
        with self._session.begin() as session:
            for table in (CertificationRow, AttestationRow):
                session.execute(delete(table))


def build_certification_store(settings: Settings | None = None) -> CertificationStore:
    """Build the configured certification store (in-memory by default)."""
    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresCertificationStore(create_engine_from_settings(resolved))
    return InMemoryCertificationStore()
