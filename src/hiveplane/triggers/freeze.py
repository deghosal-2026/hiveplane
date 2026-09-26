"""Maintenance/freeze windows: suppression and graceful drain (M28-05, D23).

A freeze is a scoped window (tenant, team, or workload) that pauses triggers and
drains running work. Matching events are suppressed and recorded as
``suppressed_freeze``; in-flight runs reach a terminal state gracefully (pause)
unless the freeze declares ``drain: abort``, which stops them. Every freeze is
attributed to the operator who declared it.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime
from enum import StrEnum
from typing import Protocol

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator
from sqlalchemy import Engine, delete, select

from hiveplane.config import Settings, get_settings
from hiveplane.core.run import Run, RunState
from hiveplane.execution.models import InterventionAction
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import TriggerFreezeRow
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext
from hiveplane.tenancy.context import DEFAULT_TENANT_ID

_ACTIVE_STATES = (RunState.RUNNING, RunState.QUEUED)


class FreezeScope(StrEnum):
    """What a freeze covers."""

    TENANT = "tenant"
    TEAM = "team"
    WORKLOAD = "workload"


class FreezeDrain(StrEnum):
    """How in-flight work is handled when a freeze starts."""

    GRACEFUL = "graceful"
    ABORT = "abort"


class FreezeSpec(BaseModel):
    """A declared maintenance/freeze window."""

    model_config = ConfigDict(extra="forbid")

    freeze_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    scope: FreezeScope
    scope_ref: str | None = Field(default=None, max_length=253)
    starts_at: AwareDatetime
    ends_at: AwareDatetime | None = None
    drain: FreezeDrain = FreezeDrain.GRACEFUL
    declared_by: str = Field(min_length=1, max_length=253)
    reason: str | None = Field(default=None, max_length=2000)

    @model_validator(mode="after")
    def _scope_and_window(self) -> FreezeSpec:
        if self.scope is not FreezeScope.TENANT and not self.scope_ref:
            raise ValueError(f"{self.scope.value} freeze requires scope_ref")
        if self.ends_at is not None and self.ends_at <= self.starts_at:
            raise ValueError("freeze ends_at must be after starts_at")
        return self

    def covers(self, *, workload: str, team: str | None) -> bool:
        """Return True when this freeze covers a workload/team."""
        if self.scope is FreezeScope.TENANT:
            return True
        if self.scope is FreezeScope.WORKLOAD:
            return workload == self.scope_ref
        return team is not None and team == self.scope_ref

    def active_at(self, when: datetime) -> bool:
        """Return True when the freeze window is active at ``when``."""
        if when < self.starts_at:
            return False
        return self.ends_at is None or when < self.ends_at


class RunIntervener(Protocol):
    """The run surface the freeze drain acts through."""

    def list_runs(self, *, ctx: TenantContext) -> list[Run]: ...

    def intervene(
        self,
        run_id: str,
        action: InterventionAction,
        *,
        actor: str,
        ctx: TenantContext,
    ) -> Run: ...


class FreezeStore(Protocol):
    """Storage for freeze windows, scoped by tenant context."""

    def save(self, freeze: FreezeSpec, *, ctx: TenantContext = ...) -> None: ...

    def get(self, freeze_id: str, *, ctx: TenantContext = ...) -> FreezeSpec | None: ...

    def list_all(self, *, ctx: TenantContext = ...) -> list[FreezeSpec]: ...

    def delete(self, freeze_id: str, *, ctx: TenantContext = ...) -> bool: ...


class InMemoryFreezeStore:
    """A process-local, thread-safe freeze store."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._freezes: dict[str, tuple[FreezeSpec, str]] = {}

    def save(self, freeze: FreezeSpec, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(freeze.tenant_id)
        with self._lock:
            self._freezes[freeze.freeze_id] = (freeze.model_copy(deep=True), freeze.tenant_id)

    def get(self, freeze_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT) -> FreezeSpec | None:
        with self._lock:
            entry = self._freezes.get(freeze_id)
            if entry is None or not ctx.scopes(entry[1]):
                return None
            return entry[0].model_copy(deep=True)

    def list_all(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> list[FreezeSpec]:
        with self._lock:
            freezes = [
                freeze for freeze, tenant in self._freezes.values() if ctx.scopes(tenant)
            ]
            freezes.sort(key=lambda freeze: (freeze.starts_at, freeze.freeze_id))
            return [freeze.model_copy(deep=True) for freeze in freezes]

    def delete(self, freeze_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT) -> bool:
        with self._lock:
            entry = self._freezes.get(freeze_id)
            if entry is None or not ctx.scopes(entry[1]):
                return False
            del self._freezes[freeze_id]
            return True


class PostgresFreezeStore:
    """A durable freeze store backed by PostgreSQL (M28-05)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def save(self, freeze: FreezeSpec, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(freeze.tenant_id)
        with self._session.begin() as session:
            row = session.get(TriggerFreezeRow, freeze.freeze_id)
            if row is None:
                session.add(
                    TriggerFreezeRow(
                        freeze_id=freeze.freeze_id,
                        tenant_id=freeze.tenant_id,
                        scope=freeze.scope.value,
                        scope_ref=freeze.scope_ref,
                        starts_at=freeze.starts_at,
                        ends_at=freeze.ends_at,
                        drain=freeze.drain.value,
                        declared_by=freeze.declared_by,
                        payload=freeze.model_dump(mode="json"),
                    )
                )
            else:
                row.scope = freeze.scope.value
                row.scope_ref = freeze.scope_ref
                row.starts_at = freeze.starts_at
                row.ends_at = freeze.ends_at
                row.drain = freeze.drain.value
                row.declared_by = freeze.declared_by
                row.payload = freeze.model_dump(mode="json")

    def get(self, freeze_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT) -> FreezeSpec | None:
        with self._session() as session:
            row = session.get(TriggerFreezeRow, freeze_id)
            if row is None or not ctx.scopes(row.tenant_id):
                return None
            return FreezeSpec.model_validate(row.payload)

    def list_all(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> list[FreezeSpec]:
        statement = select(TriggerFreezeRow).order_by(
            TriggerFreezeRow.starts_at, TriggerFreezeRow.freeze_id
        )
        if not ctx.is_system:
            statement = statement.where(TriggerFreezeRow.tenant_id == ctx.tenant_id)
        with self._session() as session:
            return [FreezeSpec.model_validate(row.payload) for row in session.scalars(statement)]

    def delete(self, freeze_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT) -> bool:
        with self._session.begin() as session:
            row = session.get(TriggerFreezeRow, freeze_id)
            if row is None or not ctx.scopes(row.tenant_id):
                return False
            session.delete(row)
            return True

    def clear(self) -> None:
        with self._session.begin() as session:
            session.execute(delete(TriggerFreezeRow))


class FreezeService:
    """Declares, lifts, checks, and drains freeze windows."""

    def __init__(
        self, store: FreezeStore, *, clock: Callable[[], datetime] | None = None
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))

    def declare(self, freeze: FreezeSpec, *, ctx: TenantContext = DEFAULT_CONTEXT) -> FreezeSpec:
        """Declare (or replace) a freeze window."""
        self._store.save(freeze, ctx=ctx)
        return freeze

    def lift(self, freeze_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT) -> bool:
        """End a freeze window immediately."""
        return self._store.delete(freeze_id, ctx=ctx)

    def list_all(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> list[FreezeSpec]:
        """Return every declared freeze window."""
        return self._store.list_all(ctx=ctx)

    def active_for(
        self,
        workload: str,
        *,
        team: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> FreezeSpec | None:
        """Return an active freeze covering ``workload``, or None."""
        now = self._clock()
        for freeze in self._store.list_all(ctx=ctx):
            if freeze.active_at(now) and freeze.covers(workload=workload, team=team):
                return freeze
        return None

    def drain(
        self,
        runs: RunIntervener,
        freeze: FreezeSpec,
        *,
        team: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[str]:
        """Pause (graceful) or stop (abort) matching in-flight runs."""
        acted: list[str] = []
        for run in runs.list_runs(ctx=ctx):
            if run.state not in _ACTIVE_STATES:
                continue
            if not freeze.covers(workload=run.workload_id, team=run.team_id or team):
                continue
            if freeze.drain is FreezeDrain.ABORT or run.state is RunState.RUNNING:
                action = (
                    InterventionAction.STOP
                    if freeze.drain is FreezeDrain.ABORT
                    else InterventionAction.PAUSE
                )
                runs.intervene(
                    run.id, action, actor=f"freeze:{freeze.freeze_id}", ctx=ctx
                )
                acted.append(run.id)
        return acted


def build_freeze_store(settings: Settings | None = None) -> FreezeStore:
    """Build the configured freeze store (in-memory by default)."""
    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresFreezeStore(create_engine_from_settings(resolved))
    return InMemoryFreezeStore()
