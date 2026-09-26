"""Storage for the attestation transparency log (M35-01).

The log is a single global, append-only, hash-chained sequence: one entry per
certification. It is deliberately not tenant-scoped — it is the canonical public
record — so no ``TenantContext`` is threaded through these methods.
"""

from __future__ import annotations

import threading
from typing import Protocol

from sqlalchemy import Engine, delete, select

from hiveplane.config import Settings, get_settings
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import AttestationLogRow
from hiveplane.transparency.models import TransparencyEntry


class TransparencyStore(Protocol):
    """Storage interface for transparency log entries."""

    def add_entry(self, entry: TransparencyEntry) -> None: ...

    def get_entry(self, attestation_id: str) -> TransparencyEntry | None: ...

    def list_entries(self) -> list[TransparencyEntry]: ...

    def delete_entry(self, attestation_id: str) -> None: ...

    def latest(self) -> TransparencyEntry | None: ...

    def clear(self) -> None: ...


class InMemoryTransparencyStore:
    """A process-local, thread-safe transparency store."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._entries: list[TransparencyEntry] = []

    def add_entry(self, entry: TransparencyEntry) -> None:
        with self._lock:
            for index, existing in enumerate(self._entries):
                if existing.attestation_id == entry.attestation_id:
                    self._entries[index] = entry.model_copy(deep=True)
                    return
            self._entries.append(entry.model_copy(deep=True))

    def get_entry(self, attestation_id: str) -> TransparencyEntry | None:
        with self._lock:
            for entry in self._entries:
                if entry.attestation_id == attestation_id:
                    return entry.model_copy(deep=True)
            return None

    def list_entries(self) -> list[TransparencyEntry]:
        with self._lock:
            return [entry.model_copy(deep=True) for entry in self._entries]

    def delete_entry(self, attestation_id: str) -> None:
        with self._lock:
            self._entries = [
                entry for entry in self._entries if entry.attestation_id != attestation_id
            ]

    def latest(self) -> TransparencyEntry | None:
        with self._lock:
            if not self._entries:
                return None
            return self._entries[-1].model_copy(deep=True)

    def clear(self) -> None:
        with self._lock:
            self._entries.clear()


class PostgresTransparencyStore:
    """A durable transparency store backed by PostgreSQL (M35-01)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def add_entry(self, entry: TransparencyEntry) -> None:
        payload = entry.model_dump(mode="json")
        with self._session.begin() as session:
            row = session.scalars(
                select(AttestationLogRow).where(
                    AttestationLogRow.attestation_id == entry.attestation_id
                )
            ).first()
            if row is None:
                session.add(
                    AttestationLogRow(
                        seq=entry.seq,
                        attestation_id=entry.attestation_id,
                        prev_hash=entry.prev_hash,
                        entry_hash=entry.entry_hash,
                        created_at=entry.created_at,
                        payload=payload,
                    )
                )
            else:
                row.seq = entry.seq
                row.prev_hash = entry.prev_hash
                row.entry_hash = entry.entry_hash
                row.created_at = entry.created_at
                row.payload = payload

    def get_entry(self, attestation_id: str) -> TransparencyEntry | None:
        statement = select(AttestationLogRow).where(
            AttestationLogRow.attestation_id == attestation_id
        )
        with self._session() as session:
            row = session.scalars(statement).first()
            if row is None:
                return None
            return TransparencyEntry.model_validate(row.payload)

    def list_entries(self) -> list[TransparencyEntry]:
        statement = select(AttestationLogRow).order_by(AttestationLogRow.seq)
        with self._session() as session:
            return [
                TransparencyEntry.model_validate(row.payload)
                for row in session.scalars(statement)
            ]

    def delete_entry(self, attestation_id: str) -> None:
        with self._session.begin() as session:
            row = session.scalars(
                select(AttestationLogRow).where(
                    AttestationLogRow.attestation_id == attestation_id
                )
            ).first()
            if row is not None:
                session.delete(row)

    def latest(self) -> TransparencyEntry | None:
        statement = select(AttestationLogRow).order_by(AttestationLogRow.seq.desc()).limit(1)
        with self._session() as session:
            row = session.scalars(statement).first()
            if row is None:
                return None
            return TransparencyEntry.model_validate(row.payload)

    def clear(self) -> None:
        with self._session.begin() as session:
            session.execute(delete(AttestationLogRow))


def build_transparency_store(settings: Settings | None = None) -> TransparencyStore:
    """Build the configured transparency store (in-memory by default)."""
    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresTransparencyStore(create_engine_from_settings(resolved))
    return InMemoryTransparencyStore()
