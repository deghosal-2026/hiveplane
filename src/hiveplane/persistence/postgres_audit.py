"""PostgreSQL-backed tamper-evident audit log (M18)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

from sqlalchemy import Engine, delete, select

from hiveplane.persistence.audit import AuditChain, AuditRecord
from hiveplane.persistence.base import session_factory
from hiveplane.persistence.models import AuditRow
from hiveplane.tenancy import DEFAULT_CONTEXT, SYSTEM_CONTEXT, TenantContext


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
            rows = session.scalars(statement)
            return [
                AuditRecord(
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
                for row in rows
            ]

    def verify(self) -> bool:
        """Return True when the persisted chain is intact."""
        return AuditChain.verify(self.records(ctx=SYSTEM_CONTEXT)) is None

    def clear(self) -> None:
        """Delete all audit records; used by tests and destructive operations."""
        with self._session.begin() as session:
            session.execute(delete(AuditRow))
