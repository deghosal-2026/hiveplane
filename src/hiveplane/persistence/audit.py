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

    def prune(
        self,
        *,
        before: datetime,
        protect: Callable[[AuditRecord], bool] | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> int: ...

    def anchor(self) -> str: ...

    def verify(self) -> bool: ...


class ScrubResultLike(Protocol):
    """Minimal result of a scrub operation (decouples audit from reporting)."""

    text: str


class TextScrubber(Protocol):
    """Minimal PII scrubber boundary used by the audit hook."""

    def scrub(self, text: str | None) -> ScrubResultLike: ...


class ScrubbingAuditLog:
    """An audit log that redacts ``detail`` before it is hashed and stored.

    Scrubbing happens before the wrapped backend computes the hash, so the
    stored chain verifies over the redacted content and raw PII is never
    persisted or retrievable through :meth:`records`.
    """

    def __init__(self, audit_log: AuditLog, scrubber: TextScrubber) -> None:
        self._log = audit_log
        self._scrubber = scrubber

    def append(
        self,
        actor: str,
        action: str,
        subject: str,
        *,
        detail: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> AuditRecord:
        """Redact ``detail``, then append so the hash covers redacted content."""
        scrubbed = None if detail is None else self._scrubber.scrub(detail).text
        return self._log.append(actor, action, subject, detail=scrubbed, ctx=ctx)

    def records(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> list[AuditRecord]:
        """Return the tenant's stored (already redacted) audit records."""
        return self._log.records(ctx=ctx)

    def prune(
        self,
        *,
        before: datetime,
        protect: Callable[[AuditRecord], bool] | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> int:
        """Delegate pruning to the wrapped log."""
        return self._log.prune(before=before, protect=protect, ctx=ctx)

    def anchor(self) -> str:
        """Return the wrapped log's chain anchor."""
        return self._log.anchor()

    def verify(self) -> bool:
        """Return True when the wrapped chain is intact."""
        return self._log.verify()


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
    def verify(records: list[AuditRecord], *, anchor: str = _NULL_HASH) -> int | None:
        """Return the index of the first broken link, or None when intact.

        ``anchor`` is the chain genesis: the hash of the last pruned record, or
        the null hash when nothing has been pruned.
        """
        prev = anchor
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
        self._anchor = _NULL_HASH
        self._sequence = 0

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
        prev_hash = self._records[-1].hash if self._records else self._anchor
        draft = AuditRecord(
            sequence=self._sequence,
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
        self._sequence += 1
        return record

    def records(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> list[AuditRecord]:
        """Return a copy of the tenant's audit records."""
        return [
            record.model_copy(deep=True)
            for record in self._records
            if ctx.scopes(record.tenant_id)
        ]

    def prune(
        self,
        *,
        before: datetime,
        protect: Callable[[AuditRecord], bool] | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> int:
        """Delete the global leading prefix older than ``before``, stopping at ``protect``."""
        deleted = 0
        while self._records:
            head = self._records[0]
            if head.created_at >= before:
                break
            if protect is not None and protect(head):
                break
            self._anchor = head.hash
            self._records.pop(0)
            deleted += 1
        return deleted

    def anchor(self) -> str:
        """Return the hash of the last pruned record (the chain genesis)."""
        return self._anchor

    def verify(self) -> bool:
        """Return True when the whole chain is intact from its anchor."""
        return AuditChain.verify(self._records, anchor=self._anchor) is None
