"""PostgreSQL-backed tamper-evident audit log (M18)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session

from hiveplane.persistence.audit import _NULL_HASH, AuditChain, AuditRecord
from hiveplane.persistence.base import session_factory
from hiveplane.persistence.models import AuditAnchorRow, AuditRow
from hiveplane.tenancy import DEFAULT_CONTEXT, SYSTEM_CONTEXT, TenantContext

_ANCHOR_ID = 1


class PostgresAuditLog:
    """A durable, tamper-evident audit log backed by PostgreSQL.

    The hash chain is global: ``tenant_id`` is stored on each row but is not
    part of the canonical hash, so chains persisted before v0.2.0 still verify.
    """

    def __init__(self, engine: Engine, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._session = session_factory(engine)

    def append(
        self,
        actor: str,
        action: str,
        subject: str,
        *,
        detail: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> AuditRecord:
        """Append a linked audit entry."""
        with self._session.begin() as session:
            last = session.scalars(
                select(AuditRow).order_by(AuditRow.sequence.desc()).limit(1)
            ).first()
            prev_hash = last.hash if last is not None else self.anchor()
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
                tenant_id=ctx.tenant_id,
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
                    tenant_id=record.tenant_id,
                    payload=record.model_dump(mode="json"),
                )
            )
            return record

    def records(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> list[AuditRecord]:
        """Return the tenant's audit records in chain order.

        The hashed fields are read from the authoritative columns (not the
        denormalized ``payload``) so that tampering with a column is reflected
        in the reconstructed record and breaks :meth:`verify`.
        """
        statement = select(AuditRow).order_by(AuditRow.sequence)
        if not ctx.is_system:
            statement = statement.where(AuditRow.tenant_id == ctx.tenant_id)
        with self._session() as session:
            return [self._record_from_row(row) for row in session.scalars(statement)]

    def prune(
        self,
        *,
        before: datetime,
        protect: Callable[[AuditRecord], bool] | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> int:
        """Delete the global leading prefix older than ``before``, stopping at ``protect``."""
        _ = ctx
        with self._session.begin() as session:
            rows = list(session.scalars(select(AuditRow).order_by(AuditRow.sequence)))
            anchor = self._read_anchor(session)
            deleted = 0
            last_sequence: int | None = None
            for row in rows:
                if row.created_at >= before:
                    break
                if protect is not None and protect(self._record_from_row(row)):
                    break
                anchor = row.hash
                last_sequence = row.sequence
                deleted += 1
            if last_sequence is not None:
                session.execute(
                    delete(AuditRow).where(AuditRow.sequence <= last_sequence)
                )
                self._write_anchor(session, anchor)
            return deleted

    def anchor(self) -> str:
        """Return the hash of the last pruned record (the chain genesis)."""
        with self._session() as session:
            return self._read_anchor(session)

    def verify(self) -> bool:
        """Return True when the persisted chain is intact from its anchor."""
        return (
            AuditChain.verify(self.records(ctx=SYSTEM_CONTEXT), anchor=self.anchor())
            is None
        )

    def clear(self) -> None:
        """Delete all audit records and reset the anchor; used by tests and ops."""
        with self._session.begin() as session:
            session.execute(delete(AuditRow))
            session.execute(delete(AuditAnchorRow))

    @staticmethod
    def _record_from_row(row: AuditRow) -> AuditRecord:
        return AuditRecord(
            sequence=row.sequence,
            actor=row.actor,
            action=row.action,
            subject=row.subject,
            created_at=row.created_at,
            detail=cast("str | None", row.payload.get("detail")),
            prev_hash=row.prev_hash,
            hash=row.hash,
            tenant_id=row.tenant_id,
        )

    @staticmethod
    def _read_anchor(session: Session) -> str:
        row = session.get(AuditAnchorRow, _ANCHOR_ID)
        return _NULL_HASH if row is None else row.anchor

    @staticmethod
    def _write_anchor(session: Session, anchor: str) -> None:
        row = session.get(AuditAnchorRow, _ANCHOR_ID)
        if row is None:
            session.add(AuditAnchorRow(id=_ANCHOR_ID, anchor=anchor))
        else:
            row.anchor = anchor
