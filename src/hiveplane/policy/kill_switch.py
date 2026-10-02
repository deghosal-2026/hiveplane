"""Tool kill switch: instant fleet-wide disable with audit (M40-06)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy import Engine, delete, select

from hiveplane.config import Settings, get_settings
from hiveplane.persistence.audit import AuditLog
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import ToolKillSwitchRow


class KillSwitchRecord(BaseModel):
    """The current kill-switch state of a tool."""

    model_config = ConfigDict(extra="forbid")

    tool_id: str = Field(min_length=1)
    disabled: bool
    reason: str | None = None
    actor: str = Field(min_length=1)
    changed_at: datetime


class KillSwitchStore(Protocol):
    """Storage interface for the tool kill switch."""

    def save(self, record: KillSwitchRecord) -> None: ...

    def get(self, tool_id: str) -> KillSwitchRecord | None: ...

    def list(self) -> list[KillSwitchRecord]: ...

    def clear(self) -> None: ...


class InMemoryKillSwitchStore:
    """A process-local, thread-safe kill-switch store."""

    def __init__(self) -> None:
        self._records: dict[str, KillSwitchRecord] = {}

    def save(self, record: KillSwitchRecord) -> None:
        self._records[record.tool_id] = record.model_copy(deep=True)

    def get(self, tool_id: str) -> KillSwitchRecord | None:
        record = self._records.get(tool_id)
        return None if record is None else record.model_copy(deep=True)

    def list(self) -> list[KillSwitchRecord]:
        records = [record.model_copy(deep=True) for record in self._records.values()]
        records.sort(key=lambda record: record.tool_id)
        return records

    def clear(self) -> None:
        self._records.clear()


class PostgresKillSwitchStore:
    """A durable kill-switch store backed by PostgreSQL (M40-06)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def save(self, record: KillSwitchRecord) -> None:
        with self._session.begin() as session:
            row = session.get(ToolKillSwitchRow, record.tool_id)
            payload = record.model_dump(mode="json")
            if row is None:
                session.add(
                    ToolKillSwitchRow(
                        tool_id=record.tool_id,
                        disabled=record.disabled,
                        reason=record.reason,
                        actor=record.actor,
                        changed_at=record.changed_at,
                        payload=payload,
                    )
                )
            else:
                row.disabled = record.disabled
                row.reason = record.reason
                row.actor = record.actor
                row.changed_at = record.changed_at
                row.payload = payload

    def get(self, tool_id: str) -> KillSwitchRecord | None:
        with self._session() as session:
            row = session.get(ToolKillSwitchRow, tool_id)
            if row is None:
                return None
            return KillSwitchRecord.model_validate(row.payload)

    def list(self) -> list[KillSwitchRecord]:
        statement = select(ToolKillSwitchRow).order_by(ToolKillSwitchRow.tool_id)
        with self._session() as session:
            return [
                KillSwitchRecord.model_validate(row.payload)
                for row in session.scalars(statement)
            ]

    def clear(self) -> None:
        with self._session.begin() as session:
            session.execute(delete(ToolKillSwitchRow))


class KillSwitch:
    """Controls fleet-wide tool disablement (fail-closed at the boundary)."""

    def __init__(
        self,
        store: KillSwitchStore,
        *,
        audit: AuditLog | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._audit = audit
        self._clock = clock or (lambda: datetime.now(UTC))

    def bind_audit(self, audit: AuditLog) -> None:
        """Bind the audit log after construction (wiring order)."""
        self._audit = audit

    def disable(self, tool_id: str, *, actor: str, reason: str | None = None) -> KillSwitchRecord:
        """Disable a tool fleet-wide, audited."""
        return self._set(tool_id, disabled=True, actor=actor, reason=reason)

    def enable(self, tool_id: str, *, actor: str) -> KillSwitchRecord:
        """Re-enable a tool, audited."""
        return self._set(tool_id, disabled=False, actor=actor, reason=None)

    def is_disabled(self, tool_id: str) -> bool:
        """Return True only if the tool is explicitly disabled (fail-open to policy)."""
        record = self._store.get(tool_id)
        return bool(record and record.disabled)

    def records(self) -> list[KillSwitchRecord]:
        """Return all kill-switch records."""
        return self._store.list()

    def disabled_tools(self) -> list[str]:
        """Return the ids of disabled tools."""
        return [record.tool_id for record in self._store.list() if record.disabled]

    def _set(
        self, tool_id: str, *, disabled: bool, actor: str, reason: str | None
    ) -> KillSwitchRecord:
        record = KillSwitchRecord(
            tool_id=tool_id,
            disabled=disabled,
            reason=reason,
            actor=actor,
            changed_at=self._clock(),
        )
        self._store.save(record)
        if self._audit is not None:
            action = "tool.disabled" if disabled else "tool.enabled"
            self._audit.append(
                actor, action, tool_id, detail=reason or "kill switch"
            )
        return record


def build_kill_switch_store(settings: Settings | None = None) -> KillSwitchStore:
    """Build the configured kill-switch store (in-memory by default)."""
    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresKillSwitchStore(create_engine_from_settings(resolved))
    return InMemoryKillSwitchStore()
