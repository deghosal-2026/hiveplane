"""Registry storage abstraction and the in-memory implementation.

Part 9 (M18) adds the PostgreSQL-backed implementation of :class:`RegistryStore`.
Until then the registry runs on an in-memory store that satisfies the same
protocol, so the API and service layers are storage-agnostic.
"""

from __future__ import annotations

import threading
from typing import Protocol

from sqlalchemy import Engine, delete, select

from hiveplane.certification.models import Attestation
from hiveplane.config import Settings, get_settings
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import (
    AttestationRow,
    ToolRow,
    TriggerRuleRow,
    WorkloadRow,
    WorkloadVersionRow,
)
from hiveplane.registry.models import (
    ToolRecord,
    TriggerRecord,
    WorkloadRecord,
    WorkloadVersion,
)


class RegistryStore(Protocol):
    """Storage interface for registry desired state and certification records."""

    def save_workload(self, record: WorkloadRecord) -> None: ...

    def get_workload(self, name: str) -> WorkloadRecord | None: ...

    def list_workloads(self) -> list[WorkloadRecord]: ...

    def delete_workload(self, name: str) -> bool: ...

    def add_version(self, version: WorkloadVersion) -> None: ...

    def list_versions(self, workload: str) -> list[WorkloadVersion]: ...

    def get_version(self, workload: str, version: int) -> WorkloadVersion | None: ...

    def save_version(self, version: WorkloadVersion) -> None: ...

    def add_attestation(self, attestation: Attestation) -> None: ...

    def get_attestation(self, attestation_id: str) -> Attestation | None: ...

    def list_attestations(self, workload: str) -> list[Attestation]: ...

    def save_tool(self, tool: ToolRecord) -> None: ...

    def get_tool(self, tool_id: str) -> ToolRecord | None: ...

    def list_tools(self) -> list[ToolRecord]: ...

    def add_trigger(self, trigger: TriggerRecord) -> None: ...

    def list_triggers(self, workload: str) -> list[TriggerRecord]: ...

    def delete_trigger(self, workload: str, trigger_id: str) -> bool: ...


class InMemoryRegistryStore:
    """A process-local, thread-safe registry store.

    Objects are copied on write and on read so callers cannot mutate stored
    state (attestations in particular must be immutable).
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._workloads: dict[str, WorkloadRecord] = {}
        self._versions: dict[str, dict[int, WorkloadVersion]] = {}
        self._attestations: dict[str, Attestation] = {}
        self._tools: dict[str, ToolRecord] = {}
        self._triggers: dict[str, dict[str, TriggerRecord]] = {}

    def save_workload(self, record: WorkloadRecord) -> None:
        with self._lock:
            self._workloads[record.name] = record.model_copy(deep=True)

    def get_workload(self, name: str) -> WorkloadRecord | None:
        with self._lock:
            record = self._workloads.get(name)
            return record.model_copy(deep=True) if record is not None else None

    def list_workloads(self) -> list[WorkloadRecord]:
        with self._lock:
            return [record.model_copy(deep=True) for record in self._workloads.values()]

    def delete_workload(self, name: str) -> bool:
        with self._lock:
            if name not in self._workloads:
                return False
            del self._workloads[name]
            self._versions.pop(name, None)
            self._triggers.pop(name, None)
            return True

    def add_version(self, version: WorkloadVersion) -> None:
        with self._lock:
            self._versions.setdefault(version.workload, {})[version.version] = (
                version.model_copy(deep=True)
            )

    def save_version(self, version: WorkloadVersion) -> None:
        self.add_version(version)

    def list_versions(self, workload: str) -> list[WorkloadVersion]:
        with self._lock:
            versions = self._versions.get(workload, {})
            ordered = sorted(versions.values(), key=lambda item: item.version)
            return [version.model_copy(deep=True) for version in ordered]

    def get_version(self, workload: str, version: int) -> WorkloadVersion | None:
        with self._lock:
            record = self._versions.get(workload, {}).get(version)
            return record.model_copy(deep=True) if record is not None else None

    def add_attestation(self, attestation: Attestation) -> None:
        with self._lock:
            self._attestations[attestation.attestation_id] = attestation.model_copy(deep=True)

    def get_attestation(self, attestation_id: str) -> Attestation | None:
        with self._lock:
            attestation = self._attestations.get(attestation_id)
            return attestation.model_copy(deep=True) if attestation is not None else None

    def list_attestations(self, workload: str) -> list[Attestation]:
        with self._lock:
            matching = [
                attestation
                for attestation in self._attestations.values()
                if attestation.workload_id == workload
            ]
            matching.sort(key=lambda item: item.timestamp)
            return [attestation.model_copy(deep=True) for attestation in matching]

    def save_tool(self, tool: ToolRecord) -> None:
        with self._lock:
            self._tools[tool.tool_id] = tool.model_copy(deep=True)

    def get_tool(self, tool_id: str) -> ToolRecord | None:
        with self._lock:
            tool = self._tools.get(tool_id)
            return tool.model_copy(deep=True) if tool is not None else None

    def list_tools(self) -> list[ToolRecord]:
        with self._lock:
            return [tool.model_copy(deep=True) for tool in self._tools.values()]

    def add_trigger(self, trigger: TriggerRecord) -> None:
        with self._lock:
            self._triggers.setdefault(trigger.workload, {})[trigger.trigger_id] = (
                trigger.model_copy(deep=True)
            )

    def list_triggers(self, workload: str) -> list[TriggerRecord]:
        with self._lock:
            triggers = self._triggers.get(workload, {}).values()
            ordered = sorted(triggers, key=lambda item: item.trigger_id)
            return [trigger.model_copy(deep=True) for trigger in ordered]

    def delete_trigger(self, workload: str, trigger_id: str) -> bool:
        with self._lock:
            triggers = self._triggers.get(workload)
            if not triggers or trigger_id not in triggers:
                return False
            del triggers[trigger_id]
            return True


class PostgresRegistryStore:
    """A durable registry store backed by PostgreSQL (#118).

    Typed columns carry the fields used for fleet and history queries; each row's
    ``payload`` stores the exact pydantic record so reads are lossless.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def save_workload(self, record: WorkloadRecord) -> None:
        """Insert or update a workload's current state."""
        with self._session.begin() as session:
            row = session.get(WorkloadRow, record.name)
            if row is None:
                session.add(
                    WorkloadRow(
                        name=record.name,
                        owner=record.owner,
                        team=record.team,
                        certification_status=record.certification_status.value,
                        current_version=record.current_version,
                        created_at=record.created_at,
                        updated_at=record.updated_at,
                        payload=record.model_dump(mode="json"),
                    )
                )
            else:
                row.owner = record.owner
                row.team = record.team
                row.certification_status = record.certification_status.value
                row.current_version = record.current_version
                row.created_at = record.created_at
                row.updated_at = record.updated_at
                row.payload = record.model_dump(mode="json")

    def get_workload(self, name: str) -> WorkloadRecord | None:
        """Return a workload by name, or ``None``."""
        with self._session() as session:
            row = session.get(WorkloadRow, name)
            return WorkloadRecord.model_validate(row.payload) if row is not None else None

    def list_workloads(self) -> list[WorkloadRecord]:
        """Return all workloads, oldest first."""
        with self._session() as session:
            rows = session.scalars(
                select(WorkloadRow).order_by(WorkloadRow.created_at, WorkloadRow.name)
            )
            return [WorkloadRecord.model_validate(row.payload) for row in rows]

    def delete_workload(self, name: str) -> bool:
        """Delete a workload, its version history, and its triggers."""
        with self._session.begin() as session:
            row = session.get(WorkloadRow, name)
            if row is None:
                return False
            session.execute(delete(TriggerRuleRow).where(TriggerRuleRow.workload == name))
            session.execute(
                delete(WorkloadVersionRow).where(WorkloadVersionRow.workload == name)
            )
            session.delete(row)
            return True

    def add_version(self, version: WorkloadVersion) -> None:
        """Insert or replace a workload version."""
        with self._session.begin() as session:
            row = session.scalars(
                select(WorkloadVersionRow).where(
                    WorkloadVersionRow.workload == version.workload,
                    WorkloadVersionRow.version == version.version,
                )
            ).first()
            if row is None:
                session.add(
                    WorkloadVersionRow(
                        workload=version.workload,
                        version=version.version,
                        created_at=version.created_at,
                        payload=version.model_dump(mode="json"),
                    )
                )
            else:
                row.created_at = version.created_at
                row.payload = version.model_dump(mode="json")

    def save_version(self, version: WorkloadVersion) -> None:
        """Alias for :meth:`add_version`."""
        self.add_version(version)

    def list_versions(self, workload: str) -> list[WorkloadVersion]:
        """Return a workload's version history, oldest first."""
        with self._session() as session:
            rows = session.scalars(
                select(WorkloadVersionRow)
                .where(WorkloadVersionRow.workload == workload)
                .order_by(WorkloadVersionRow.version)
            )
            return [WorkloadVersion.model_validate(row.payload) for row in rows]

    def get_version(self, workload: str, version: int) -> WorkloadVersion | None:
        """Return one workload version, or ``None``."""
        with self._session() as session:
            row = session.scalars(
                select(WorkloadVersionRow).where(
                    WorkloadVersionRow.workload == workload,
                    WorkloadVersionRow.version == version,
                )
            ).first()
            return WorkloadVersion.model_validate(row.payload) if row is not None else None

    def add_attestation(self, attestation: Attestation) -> None:
        """Store an attestation immutably, keyed by id."""
        with self._session.begin() as session:
            row = session.get(AttestationRow, attestation.attestation_id)
            if row is None:
                session.add(
                    AttestationRow(
                        attestation_id=attestation.attestation_id,
                        workload=attestation.workload_id,
                        model_identity=attestation.model_identity,
                        created_at=attestation.timestamp,
                        payload=attestation.model_dump(mode="json"),
                    )
                )

    def get_attestation(self, attestation_id: str) -> Attestation | None:
        """Return an attestation by id, or ``None``."""
        with self._session() as session:
            row = session.get(AttestationRow, attestation_id)
            return Attestation.model_validate(row.payload) if row is not None else None

    def list_attestations(self, workload: str) -> list[Attestation]:
        """Return a workload's attestations, oldest first."""
        with self._session() as session:
            rows = session.scalars(
                select(AttestationRow)
                .where(AttestationRow.workload == workload)
                .order_by(AttestationRow.created_at)
            )
            return [Attestation.model_validate(row.payload) for row in rows]

    def save_tool(self, tool: ToolRecord) -> None:
        """Insert or update a registered tool."""
        with self._session.begin() as session:
            row = session.get(ToolRow, tool.tool_id)
            if row is None:
                session.add(
                    ToolRow(
                        tool_id=tool.tool_id,
                        mcp_server=tool.mcp_server,
                        trust_level=tool.trust_level.value,
                        registered_at=tool.registered_at,
                        payload=tool.model_dump(mode="json"),
                    )
                )
            else:
                row.mcp_server = tool.mcp_server
                row.trust_level = tool.trust_level.value
                row.registered_at = tool.registered_at
                row.payload = tool.model_dump(mode="json")

    def get_tool(self, tool_id: str) -> ToolRecord | None:
        """Return a registered tool by id, or ``None``."""
        with self._session() as session:
            row = session.get(ToolRow, tool_id)
            return ToolRecord.model_validate(row.payload) if row is not None else None

    def list_tools(self) -> list[ToolRecord]:
        """Return all tools, ordered by id."""
        with self._session() as session:
            rows = session.scalars(select(ToolRow).order_by(ToolRow.tool_id))
            return [ToolRecord.model_validate(row.payload) for row in rows]

    def add_trigger(self, trigger: TriggerRecord) -> None:
        """Insert or replace a trigger rule."""
        with self._session.begin() as session:
            row = session.get(TriggerRuleRow, trigger.trigger_id)
            if row is None:
                session.add(
                    TriggerRuleRow(
                        trigger_id=trigger.trigger_id,
                        workload=trigger.workload,
                        type=trigger.rule.type.value,
                        payload=trigger.model_dump(mode="json"),
                    )
                )
            else:
                row.workload = trigger.workload
                row.type = trigger.rule.type.value
                row.payload = trigger.model_dump(mode="json")

    def list_triggers(self, workload: str) -> list[TriggerRecord]:
        """Return a workload's triggers, ordered by id."""
        with self._session() as session:
            rows = session.scalars(
                select(TriggerRuleRow)
                .where(TriggerRuleRow.workload == workload)
                .order_by(TriggerRuleRow.trigger_id)
            )
            return [TriggerRecord.model_validate(row.payload) for row in rows]

    def delete_trigger(self, workload: str, trigger_id: str) -> bool:
        """Delete a trigger rule, returning whether it existed."""
        with self._session.begin() as session:
            row = session.get(TriggerRuleRow, trigger_id)
            if row is None or row.workload != workload:
                return False
            session.delete(row)
            return True

    def clear(self) -> None:
        """Delete all registry data; used by tests and destructive operations."""
        with self._session.begin() as session:
            for table in (
                TriggerRuleRow,
                ToolRow,
                AttestationRow,
                WorkloadVersionRow,
                WorkloadRow,
            ):
                session.execute(delete(table))


def build_registry_store(settings: Settings | None = None) -> RegistryStore:
    """Build the configured registry store (in-memory by default)."""
    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresRegistryStore(create_engine_from_settings(resolved))
    return InMemoryRegistryStore()
