"""Registry storage abstraction and the in-memory implementation.

Workload names are globally unique (v0.2.0 parked decision), but every record
is tenant-tagged and every read is filtered by the acting tenant context.
Version, attestation, and trigger rows inherit their workload's tenant, so a
cross-tenant write raises before it can violate the composite foreign keys.
"""

from __future__ import annotations

import threading
from typing import Protocol

from sqlalchemy import Engine, delete, select
from sqlalchemy.orm import Session

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
from hiveplane.registry.errors import WorkloadNotFoundError
from hiveplane.registry.models import (
    ToolRecord,
    TriggerRecord,
    WorkloadRecord,
    WorkloadVersion,
)
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext, TenantScopeError


class RegistryStore(Protocol):
    """Storage interface for registry desired state and certification records."""

    def save_workload(
        self, record: WorkloadRecord, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def get_workload(
        self, name: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> WorkloadRecord | None: ...

    def list_workloads(
        self, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[WorkloadRecord]: ...

    def delete_workload(
        self, name: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> bool: ...

    def add_version(
        self, version: WorkloadVersion, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def list_versions(
        self, workload: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[WorkloadVersion]: ...

    def get_version(
        self, workload: str, version: int, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> WorkloadVersion | None: ...

    def save_version(
        self, version: WorkloadVersion, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def add_attestation(
        self, attestation: Attestation, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def get_attestation(
        self, attestation_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> Attestation | None: ...

    def list_attestations(
        self, workload: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[Attestation]: ...

    def save_tool(
        self, tool: ToolRecord, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def get_tool(
        self, tool_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> ToolRecord | None: ...

    def list_tools(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> list[ToolRecord]: ...

    def add_trigger(
        self, trigger: TriggerRecord, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def list_triggers(
        self, workload: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[TriggerRecord]: ...

    def delete_trigger(
        self, workload: str, trigger_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> bool: ...


class InMemoryRegistryStore:
    """A process-local, thread-safe registry store.

    Objects are copied on write and on read so callers cannot mutate stored
    state (attestations in particular must be immutable). Tools carry the
    tenant of the context that registered them; workload-linked records
    inherit their workload's tenant.
    """

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._workloads: dict[str, WorkloadRecord] = {}
        self._versions: dict[str, dict[int, WorkloadVersion]] = {}
        self._attestations: dict[str, Attestation] = {}
        self._attestation_tenants: dict[str, str] = {}
        self._tools: dict[str, ToolRecord] = {}
        self._tool_tenants: dict[str, str] = {}
        self._triggers: dict[str, dict[str, TriggerRecord]] = {}

    def save_workload(
        self, record: WorkloadRecord, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        with self._lock:
            ctx.require(record.tenant_id)
            self._workloads[record.name] = record.model_copy(deep=True)

    def get_workload(
        self, name: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> WorkloadRecord | None:
        with self._lock:
            record = self._workloads.get(name)
            if record is None or not ctx.scopes(record.tenant_id):
                return None
            return record.model_copy(deep=True)

    def list_workloads(
        self, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[WorkloadRecord]:
        with self._lock:
            records = [
                record for record in self._workloads.values() if ctx.scopes(record.tenant_id)
            ]
            return [record.model_copy(deep=True) for record in records]

    def delete_workload(self, name: str, *, ctx: TenantContext = DEFAULT_CONTEXT) -> bool:
        with self._lock:
            record = self._workloads.get(name)
            if record is None or not ctx.scopes(record.tenant_id):
                return False
            del self._workloads[name]
            self._versions.pop(name, None)
            self._triggers.pop(name, None)
            return True

    def _parent(self, workload: str, ctx: TenantContext) -> WorkloadRecord:
        record = self._workloads.get(workload)
        if record is None:
            raise WorkloadNotFoundError(workload)
        ctx.require(record.tenant_id)
        return record

    def _parent_scoped(self, workload: str, ctx: TenantContext) -> WorkloadRecord | None:
        record = self._workloads.get(workload)
        if record is None or not ctx.scopes(record.tenant_id):
            return None
        return record

    def add_version(
        self, version: WorkloadVersion, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        with self._lock:
            self._parent(version.workload, ctx)
            self._versions.setdefault(version.workload, {})[version.version] = (
                version.model_copy(deep=True)
            )

    def save_version(
        self, version: WorkloadVersion, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        self.add_version(version, ctx=ctx)

    def list_versions(
        self, workload: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[WorkloadVersion]:
        with self._lock:
            if self._parent_scoped(workload, ctx) is None:
                return []
            versions = self._versions.get(workload, {})
            ordered = sorted(versions.values(), key=lambda item: item.version)
            return [version.model_copy(deep=True) for version in ordered]

    def get_version(
        self, workload: str, version: int, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> WorkloadVersion | None:
        with self._lock:
            if self._parent_scoped(workload, ctx) is None:
                return None
            record = self._versions.get(workload, {}).get(version)
            return record.model_copy(deep=True) if record is not None else None

    def add_attestation(
        self, attestation: Attestation, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        with self._lock:
            parent = self._parent(attestation.workload_id, ctx)
            self._attestations[attestation.attestation_id] = attestation.model_copy(deep=True)
            self._attestation_tenants[attestation.attestation_id] = parent.tenant_id

    def get_attestation(
        self, attestation_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> Attestation | None:
        with self._lock:
            attestation = self._attestations.get(attestation_id)
            tenant = self._attestation_tenants.get(attestation_id)
            if attestation is None or tenant is None or not ctx.scopes(tenant):
                return None
            return attestation.model_copy(deep=True)

    def list_attestations(
        self, workload: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[Attestation]:
        with self._lock:
            if self._parent_scoped(workload, ctx) is None:
                return []
            matching = [
                attestation
                for attestation_id, attestation in self._attestations.items()
                if attestation.workload_id == workload
                and ctx.scopes(self._attestation_tenants.get(attestation_id, ""))
            ]
            matching.sort(key=lambda item: item.timestamp)
            return [attestation.model_copy(deep=True) for attestation in matching]

    def save_tool(self, tool: ToolRecord, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        with self._lock:
            self._tools[tool.tool_id] = tool.model_copy(deep=True)
            self._tool_tenants[tool.tool_id] = ctx.tenant_id

    def get_tool(
        self, tool_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> ToolRecord | None:
        with self._lock:
            tool = self._tools.get(tool_id)
            tenant = self._tool_tenants.get(tool_id)
            if tool is None or tenant is None or not ctx.scopes(tenant):
                return None
            return tool.model_copy(deep=True)

    def list_tools(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> list[ToolRecord]:
        with self._lock:
            tools = [
                tool
                for tool_id, tool in self._tools.items()
                if ctx.scopes(self._tool_tenants.get(tool_id, ""))
            ]
            return [tool.model_copy(deep=True) for tool in tools]

    def add_trigger(
        self, trigger: TriggerRecord, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        with self._lock:
            self._parent(trigger.workload, ctx)
            self._triggers.setdefault(trigger.workload, {})[trigger.trigger_id] = (
                trigger.model_copy(deep=True)
            )

    def list_triggers(
        self, workload: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[TriggerRecord]:
        with self._lock:
            if self._parent_scoped(workload, ctx) is None:
                return []
            triggers = self._triggers.get(workload, {}).values()
            ordered = sorted(triggers, key=lambda item: item.trigger_id)
            return [trigger.model_copy(deep=True) for trigger in ordered]

    def delete_trigger(
        self, workload: str, trigger_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> bool:
        with self._lock:
            if self._parent_scoped(workload, ctx) is None:
                return False
            triggers = self._triggers.get(workload)
            if not triggers or trigger_id not in triggers:
                return False
            del triggers[trigger_id]
            return True


class PostgresRegistryStore:
    """A durable registry store backed by PostgreSQL (#118).

    Typed columns carry the fields used for fleet and history queries; each row's
    ``payload`` stores the exact pydantic record so reads are lossless. Version,
    attestation, and trigger rows inherit their workload's ``tenant_id`` so the
    composite foreign keys hold.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def save_workload(
        self, record: WorkloadRecord, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        """Insert or update a workload's current state."""
        ctx.require(record.tenant_id)
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
                        tenant_id=record.tenant_id,
                        payload=record.model_dump(mode="json"),
                    )
                )
            else:
                if not ctx.scopes(row.tenant_id):
                    raise WorkloadNotFoundError(record.name)
                row.owner = record.owner
                row.team = record.team
                row.certification_status = record.certification_status.value
                row.current_version = record.current_version
                row.created_at = record.created_at
                row.updated_at = record.updated_at
                row.tenant_id = record.tenant_id
                row.payload = record.model_dump(mode="json")

    def get_workload(
        self, name: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> WorkloadRecord | None:
        """Return a workload by name within the acting tenant, or ``None``."""
        with self._session() as session:
            row = session.get(WorkloadRow, name)
            if row is None or not ctx.scopes(row.tenant_id):
                return None
            return WorkloadRecord.model_validate(row.payload)

    def list_workloads(
        self, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[WorkloadRecord]:
        """Return the tenant's workloads, oldest first."""
        statement = select(WorkloadRow).order_by(WorkloadRow.created_at, WorkloadRow.name)
        if not ctx.is_system:
            statement = statement.where(WorkloadRow.tenant_id == ctx.tenant_id)
        with self._session() as session:
            return [
                WorkloadRecord.model_validate(row.payload)
                for row in session.scalars(statement)
            ]

    def delete_workload(self, name: str, *, ctx: TenantContext = DEFAULT_CONTEXT) -> bool:
        """Delete a workload, its version history, and its triggers."""
        with self._session.begin() as session:
            row = session.get(WorkloadRow, name)
            if row is None or not ctx.scopes(row.tenant_id):
                return False
            session.execute(delete(TriggerRuleRow).where(TriggerRuleRow.workload == name))
            session.execute(
                delete(WorkloadVersionRow).where(WorkloadVersionRow.workload == name)
            )
            session.delete(row)
            return True

    @staticmethod
    def _parent(session: Session, workload: str, ctx: TenantContext) -> WorkloadRow:
        row = session.get(WorkloadRow, workload)
        if row is None:
            raise WorkloadNotFoundError(workload)
        ctx.require(row.tenant_id)
        return row

    @staticmethod
    def _parent_scoped(
        session: Session, workload: str, ctx: TenantContext
    ) -> WorkloadRow | None:
        row = session.get(WorkloadRow, workload)
        if row is None or not ctx.scopes(row.tenant_id):
            return None
        return row

    def add_version(
        self, version: WorkloadVersion, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        """Insert or replace a workload version."""
        with self._session.begin() as session:
            parent = self._parent(session, version.workload, ctx)
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
                        tenant_id=parent.tenant_id,
                        payload=version.model_dump(mode="json"),
                    )
                )
            else:
                row.created_at = version.created_at
                row.payload = version.model_dump(mode="json")

    def save_version(
        self, version: WorkloadVersion, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        """Alias for :meth:`add_version`."""
        self.add_version(version, ctx=ctx)

    def list_versions(
        self, workload: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[WorkloadVersion]:
        """Return a workload's version history, oldest first."""
        with self._session() as session:
            if self._parent_scoped(session, workload, ctx) is None:
                return []
            rows = session.scalars(
                select(WorkloadVersionRow)
                .where(WorkloadVersionRow.workload == workload)
                .order_by(WorkloadVersionRow.version)
            )
            return [WorkloadVersion.model_validate(row.payload) for row in rows]

    def get_version(
        self, workload: str, version: int, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> WorkloadVersion | None:
        """Return one workload version, or ``None``."""
        with self._session() as session:
            if self._parent_scoped(session, workload, ctx) is None:
                return None
            row = session.scalars(
                select(WorkloadVersionRow).where(
                    WorkloadVersionRow.workload == workload,
                    WorkloadVersionRow.version == version,
                )
            ).first()
            return WorkloadVersion.model_validate(row.payload) if row is not None else None

    def add_attestation(
        self, attestation: Attestation, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        """Store an attestation immutably, keyed by id."""
        with self._session.begin() as session:
            if session.get(AttestationRow, attestation.attestation_id) is not None:
                return
            parent = self._parent(session, attestation.workload_id, ctx)
            session.add(
                AttestationRow(
                    attestation_id=attestation.attestation_id,
                    workload=attestation.workload_id,
                    model_identity=attestation.model_identity,
                    created_at=attestation.timestamp,
                    tenant_id=parent.tenant_id,
                    payload=attestation.model_dump(mode="json"),
                )
            )

    def get_attestation(
        self, attestation_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> Attestation | None:
        """Return an attestation by id, or ``None``."""
        with self._session() as session:
            row = session.get(AttestationRow, attestation_id)
            if row is None or not ctx.scopes(row.tenant_id):
                return None
            return Attestation.model_validate(row.payload)

    def list_attestations(
        self, workload: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[Attestation]:
        """Return a workload's attestations, oldest first."""
        with self._session() as session:
            if self._parent_scoped(session, workload, ctx) is None:
                return []
            rows = session.scalars(
                select(AttestationRow)
                .where(AttestationRow.workload == workload)
                .order_by(AttestationRow.created_at)
            )
            return [Attestation.model_validate(row.payload) for row in rows]

    def save_tool(self, tool: ToolRecord, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
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
                        tenant_id=ctx.tenant_id,
                        payload=tool.model_dump(mode="json"),
                    )
                )
            else:
                if not ctx.scopes(row.tenant_id):
                    raise TenantScopeError(
                        ctx.tenant_id, f"cannot modify tool {tool.tool_id!r}"
                    )
                row.mcp_server = tool.mcp_server
                row.trust_level = tool.trust_level.value
                row.registered_at = tool.registered_at
                row.payload = tool.model_dump(mode="json")

    def get_tool(
        self, tool_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> ToolRecord | None:
        """Return a registered tool by id, or ``None``."""
        with self._session() as session:
            row = session.get(ToolRow, tool_id)
            if row is None or not ctx.scopes(row.tenant_id):
                return None
            return ToolRecord.model_validate(row.payload)

    def list_tools(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> list[ToolRecord]:
        """Return the tenant's tools, ordered by id."""
        statement = select(ToolRow).order_by(ToolRow.tool_id)
        if not ctx.is_system:
            statement = statement.where(ToolRow.tenant_id == ctx.tenant_id)
        with self._session() as session:
            return [ToolRecord.model_validate(row.payload) for row in session.scalars(statement)]

    def add_trigger(
        self, trigger: TriggerRecord, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        """Insert or replace a trigger rule."""
        with self._session.begin() as session:
            parent = self._parent(session, trigger.workload, ctx)
            row = session.get(TriggerRuleRow, trigger.trigger_id)
            if row is None:
                session.add(
                    TriggerRuleRow(
                        trigger_id=trigger.trigger_id,
                        workload=trigger.workload,
                        type=trigger.rule.type.value,
                        tenant_id=parent.tenant_id,
                        payload=trigger.model_dump(mode="json"),
                    )
                )
            else:
                if row.workload != trigger.workload or not ctx.scopes(row.tenant_id):
                    raise TenantScopeError(
                        ctx.tenant_id, f"cannot modify trigger {trigger.trigger_id!r}"
                    )
                row.workload = trigger.workload
                row.type = trigger.rule.type.value
                row.payload = trigger.model_dump(mode="json")

    def list_triggers(
        self, workload: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[TriggerRecord]:
        """Return a workload's triggers, ordered by id."""
        with self._session() as session:
            if self._parent_scoped(session, workload, ctx) is None:
                return []
            rows = session.scalars(
                select(TriggerRuleRow)
                .where(TriggerRuleRow.workload == workload)
                .order_by(TriggerRuleRow.trigger_id)
            )
            return [TriggerRecord.model_validate(row.payload) for row in rows]

    def delete_trigger(
        self, workload: str, trigger_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> bool:
        """Delete a trigger rule, returning whether it existed."""
        with self._session.begin() as session:
            if self._parent_scoped(session, workload, ctx) is None:
                return False
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
