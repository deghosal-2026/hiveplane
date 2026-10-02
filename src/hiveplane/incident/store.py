"""Persistence for incident-mode halt records (M53)."""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import Engine, delete, select

from hiveplane.config import Settings, get_settings
from hiveplane.incident.models import IncidentRecord
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import IncidentRow


class IncidentStore(Protocol):
    """Storage interface for incident records."""

    def save(self, record: IncidentRecord) -> None: ...

    def get(self, incident_id: str) -> IncidentRecord | None: ...

    def active(self) -> IncidentRecord | None: ...

    def active_all(self) -> list[IncidentRecord]: ...

    def list(self) -> list[IncidentRecord]: ...

    def clear(self) -> None: ...


class InMemoryIncidentStore:
    """A process-local incident store."""

    def __init__(self) -> None:
        self._records: dict[str, IncidentRecord] = {}

    def save(self, record: IncidentRecord) -> None:
        self._records[record.incident_id] = record.model_copy(deep=True)

    def get(self, incident_id: str) -> IncidentRecord | None:
        record = self._records.get(incident_id)
        return None if record is None else record.model_copy(deep=True)

    def active_all(self) -> list[IncidentRecord]:
        records = [
            record.model_copy(deep=True)
            for record in self._records.values()
            if record.resumed_at is None
        ]
        records.sort(key=lambda record: record.halted_at)
        return records

    def active(self) -> IncidentRecord | None:
        active = self.active_all()
        return active[-1] if active else None

    def list(self) -> list[IncidentRecord]:
        records = [record.model_copy(deep=True) for record in self._records.values()]
        records.sort(key=lambda record: record.halted_at)
        return records

    def clear(self) -> None:
        self._records.clear()


class PostgresIncidentStore:
    """A durable incident store backed by PostgreSQL (M53)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def save(self, record: IncidentRecord) -> None:
        with self._session.begin() as session:
            row = session.get(IncidentRow, record.incident_id)
            payload = record.model_dump(mode="json")
            if row is None:
                session.add(
                    IncidentRow(
                        incident_id=record.incident_id,
                        scope=record.scope.value,
                        scope_ref=record.scope_ref,
                        trigger=record.trigger,
                        reason=record.reason,
                        actor=record.actor,
                        halted_at=record.halted_at,
                        resumed_at=record.resumed_at,
                        resumed_by=record.resumed_by,
                        payload=payload,
                    )
                )
            else:
                row.scope = record.scope.value
                row.scope_ref = record.scope_ref
                row.trigger = record.trigger
                row.reason = record.reason
                row.actor = record.actor
                row.halted_at = record.halted_at
                row.resumed_at = record.resumed_at
                row.resumed_by = record.resumed_by
                row.payload = payload

    def get(self, incident_id: str) -> IncidentRecord | None:
        with self._session() as session:
            row = session.get(IncidentRow, incident_id)
            if row is None:
                return None
            return IncidentRecord.model_validate(row.payload)

    def active_all(self) -> list[IncidentRecord]:
        statement = (
            select(IncidentRow)
            .where(IncidentRow.resumed_at.is_(None))
            .order_by(IncidentRow.halted_at)
        )
        with self._session() as session:
            return [
                IncidentRecord.model_validate(row.payload)
                for row in session.scalars(statement)
            ]

    def active(self) -> IncidentRecord | None:
        active = self.active_all()
        return active[-1] if active else None

    def list(self) -> list[IncidentRecord]:
        statement = select(IncidentRow).order_by(IncidentRow.halted_at)
        with self._session() as session:
            return [
                IncidentRecord.model_validate(row.payload)
                for row in session.scalars(statement)
            ]

    def clear(self) -> None:
        with self._session.begin() as session:
            session.execute(delete(IncidentRow))


def build_incident_store(settings: Settings | None = None) -> IncidentStore:
    """Build the configured incident store (in-memory by default)."""
    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresIncidentStore(create_engine_from_settings(resolved))
    return InMemoryIncidentStore()
