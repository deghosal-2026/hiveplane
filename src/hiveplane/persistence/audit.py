"""Tamper-evident audit log (M18, D7).

Each record's hash covers the previous hash plus the record's canonical payload,
so any edited, reordered, or deleted record breaks verification at that index.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext
from hiveplane.tenancy.context import DEFAULT_TENANT_ID

_NULL_HASH = "0" * 64


class AuditRecord(BaseModel):
    """One tamper-evident audit entry."""

    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=0)
    actor: str = Field(min_length=1)
    action: str = Field(min_length=1)
    subject: str = Field(min_length=1)
    created_at: AwareDatetime
    detail: str | None = None
    prev_hash: str
    hash: str
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)


class AuditLog(Protocol):
    """Append-only, tamper-evident audit log.

    The hash chain is global (D21: one chain, ``tenant_id`` per entry, and the
    tenant is deliberately not part of the canonical hash so chains persisted
    before v0.2.0 still verify). Reads are filtered by the acting tenant.
    """

    def append(
        self,
        actor: str,
        action: str,
        subject: str,
        *,
        detail: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> AuditRecord: ...

    def records(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> list[AuditRecord]: ...

    def verify(self) -> bool: ...


def _canonical(record: AuditRecord) -> str:
    payload = {
        "sequence": record.sequence,
        "actor": record.actor,
        "action": record.action,
        "subject": record.subject,
        "created_at": record.created_at.isoformat(),
        "detail": record.detail,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


class AuditChain:
    """Chained-hash math shared by audit backends."""

    @staticmethod
    def compute_hash(prev_hash: str, record: AuditRecord) -> str:
        """Return the hash for ``record`` given the previous link."""
        digest = hashlib.sha256(f"{prev_hash}|{_canonical(record)}".encode())
        return digest.hexdigest()

    @staticmethod
    def verify(records: list[AuditRecord]) -> int | None:
        """Return the index of the first broken link, or None when intact."""
        prev = _NULL_HASH
        for index, record in enumerate(records):
            if record.prev_hash != prev:
                return index
            if record.hash != AuditChain.compute_hash(prev, record):
                return index
            prev = record.hash
        return None


class InMemoryAuditLog:
    """An in-process tamper-evident audit log."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._records: list[AuditRecord] = []

    def append(
        self,
        actor: str,
        action: str,
        subject: str,
        *,
        detail: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> AuditRecord:
        """Append an audit entry, linking it to the previous hash."""
        prev_hash = self._records[-1].hash if self._records else _NULL_HASH
        draft = AuditRecord(
            sequence=len(self._records),
            actor=actor,
            action=action,
            subject=subject,
            created_at=self._clock(),
            detail=detail,
            prev_hash=prev_hash,
            hash="",
            tenant_id=ctx.tenant_id,
        )
        record = draft.model_copy(update={"hash": AuditChain.compute_hash(prev_hash, draft)})
        self._records.append(record)
        return record

    def records(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> list[AuditRecord]:
        """Return a copy of the tenant's audit records."""
        return [
            record.model_copy(deep=True)
            for record in self._records
            if ctx.scopes(record.tenant_id)
        ]

    def verify(self) -> bool:
        """Return True when the whole chain is intact."""
        return AuditChain.verify(self._records) is None
