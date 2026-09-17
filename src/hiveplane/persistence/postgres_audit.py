"""PostgreSQL-backed tamper-evident audit log (M18)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from sqlalchemy import Engine, delete, select

from hiveplane.persistence.audit import AuditChain, AuditRecord
from hiveplane.persistence.base import session_factory
from hiveplane.persistence.models import AuditRow


class PostgresAuditLog:
    """A durable, tamper-evident audit log backed by PostgreSQL."""

    def __init__(self, engine: Engine, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._session = session_factory(engine)

    def append(
        self, actor: str, action: str, subject: str, *, detail: str | None = None
    ) -> AuditRecord:
        """Append a linked audit entry."""
        with self._session.begin() as session:
            last = session.scalars(
                select(AuditRow).order_by(AuditRow.sequence.desc()).limit(1)
            ).first()
            prev_hash = last.hash if last is not None else "0" * 64
            sequence = last.sequence + 1 if last is not None else 1
            draft = AuditRecord(
                sequence=sequence,
                actor=actor,
                action=action,
                subject=subject,
                created_at=self._clock(),
                detail=detail,
                prev_hash=prev_hash,
                hash="",
            )
            record = draft.model_copy(
                update={"hash": AuditChain.compute_hash(prev_hash, draft)}
            )
            session.add(
                AuditRow(
                    sequence=record.sequence,
                    actor=record.actor,
                    action=record.action,
                    subject=record.subject,
                    created_at=record.created_at,
                    prev_hash=record.prev_hash,
                    hash=record.hash,
                    payload=record.model_dump(mode="json"),
                )
            )
            return record

    def records(self) -> list[AuditRecord]:
        """Return all audit records in chain order."""
        with self._session() as session:
            rows = session.scalars(select(AuditRow).order_by(AuditRow.sequence))
            return [AuditRecord.model_validate(row.payload) for row in rows]

    def verify(self) -> bool:
        """Return True when the persisted chain is intact."""
        return AuditChain.verify(self.records()) is None

    def clear(self) -> None:
        """Delete all audit records; used by tests and destructive operations."""
        with self._session.begin() as session:
            session.execute(delete(AuditRow))
