"""Reconcile store: declared specs, state, drift, and run history (M26-04).

Two implementations share one protocol: an in-memory store for tests and
single-process use, and a Postgres store for durability. Every read and write is
filtered by the acting :class:`~hiveplane.tenancy.context.TenantContext`, and
``replace_source_specs`` swaps a source's declared set atomically so a partial
revision can never be observed.
"""

from __future__ import annotations

import threading
from typing import Protocol

from sqlalchemy import Engine, delete, select

from hiveplane.config import Settings, get_settings
from hiveplane.fleet.reconcile import DesiredSpec, DriftRecord, ReconcileState
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import (
    DesiredSpecRow,
    DriftRecordRow,
    ReconcileRunRow,
    ReconcileStateRow,
)
from hiveplane.reconcile.models import ReconcileRun
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext, TenantScopeError


class ReconcileStore(Protocol):
    """Storage interface for desired state, convergence, drift, and run history."""

    def replace_source_specs(
        self, source_id: str, specs: list[DesiredSpec], *, ctx: TenantContext = ...
    ) -> None: ...

    def list_specs(
        self, source_id: str | None = None, *, ctx: TenantContext = ...
    ) -> list[DesiredSpec]: ...

    def get_state(
        self, source_id: str, *, ctx: TenantContext = ...
    ) -> ReconcileState | None: ...

    def save_state(
        self, state: ReconcileState, *, ctx: TenantContext = ...
    ) -> None: ...

    def list_states(self, *, ctx: TenantContext = ...) -> list[ReconcileState]: ...

    def add_drift(
        self, drift: DriftRecord, *, ctx: TenantContext = ...
    ) -> None: ...

    def save_drift(
        self, drift: DriftRecord, *, ctx: TenantContext = ...
    ) -> None: ...

    def list_drift(
        self,
        *,
        object_ref: str | None = None,
        reconcile_run_id: str | None = None,
        ctx: TenantContext = ...,
    ) -> list[DriftRecord]: ...

    def add_run(self, run: ReconcileRun, *, ctx: TenantContext = ...) -> None: ...

    def get_run(
        self, run_id: str, *, ctx: TenantContext = ...
    ) -> ReconcileRun | None: ...

    def list_runs(
        self,
        source_id: str | None = None,
        *,
        limit: int | None = None,
        ctx: TenantContext = ...,
    ) -> list[ReconcileRun]: ...

    def clear(self) -> None: ...


class InMemoryReconcileStore:
    """A process-local, thread-safe reconcile store."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._specs: dict[str, DesiredSpec] = {}
        self._states: dict[tuple[str, str], ReconcileState] = {}
        self._drift: dict[str, DriftRecord] = {}
        self._runs: dict[str, ReconcileRun] = {}

    def replace_source_specs(
        self, source_id: str, specs: list[DesiredSpec], *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        for spec in specs:
            ctx.require(spec.tenant_id)
        with self._lock:
            self._specs = {
                spec_id: spec
                for spec_id, spec in self._specs.items()
                if not (spec.source_id == source_id and ctx.scopes(spec.tenant_id))
            }
            for spec in specs:
                self._specs[spec.spec_id] = spec.model_copy(deep=True)

    def list_specs(
        self, source_id: str | None = None, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[DesiredSpec]:
        with self._lock:
            specs = [
                spec
                for spec in self._specs.values()
                if ctx.scopes(spec.tenant_id)
                and (source_id is None or spec.source_id == source_id)
            ]
            specs.sort(key=lambda spec: (spec.kind.value, spec.name))
            return [spec.model_copy(deep=True) for spec in specs]

    def get_state(
        self, source_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> ReconcileState | None:
        with self._lock:
            state = self._states.get((ctx.tenant_id, source_id))
            if state is None or not ctx.scopes(state.tenant_id):
                return None
            return state.model_copy(deep=True)

    def save_state(
        self, state: ReconcileState, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(state.tenant_id)
        with self._lock:
            self._states[(state.tenant_id, state.source_id)] = state.model_copy(deep=True)

    def list_states(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> list[ReconcileState]:
        with self._lock:
            states = [
                state for state in self._states.values() if ctx.scopes(state.tenant_id)
            ]
            states.sort(key=lambda state: state.source_id)
            return [state.model_copy(deep=True) for state in states]

    def add_drift(self, drift: DriftRecord, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(drift.tenant_id)
        with self._lock:
            self._drift[drift.drift_id] = drift.model_copy(deep=True)

    def save_drift(self, drift: DriftRecord, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        self.add_drift(drift, ctx=ctx)

    def list_drift(
        self,
        *,
        object_ref: str | None = None,
        reconcile_run_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[DriftRecord]:
        with self._lock:
            drift = [
                record
                for record in self._drift.values()
                if ctx.scopes(record.tenant_id)
                and (object_ref is None or record.object_ref == object_ref)
                and (
                    reconcile_run_id is None
                    or record.reconcile_run_id == reconcile_run_id
                )
            ]
            drift.sort(key=lambda record: (record.detected_at, record.drift_id))
            return [record.model_copy(deep=True) for record in drift]

    def add_run(self, run: ReconcileRun, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(run.tenant_id)
        with self._lock:
            self._runs[run.run_id] = run.model_copy(deep=True)

    def get_run(
        self, run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> ReconcileRun | None:
        with self._lock:
            run = self._runs.get(run_id)
            if run is None or not ctx.scopes(run.tenant_id):
                return None
            return run.model_copy(deep=True)

    def list_runs(
        self,
        source_id: str | None = None,
        *,
        limit: int | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[ReconcileRun]:
        with self._lock:
            runs = [
                run
                for run in self._runs.values()
                if ctx.scopes(run.tenant_id)
                and (source_id is None or run.source_id == source_id)
            ]
            runs.sort(key=lambda run: (run.started_at, run.run_id))
            if limit is not None:
                runs = runs[-limit:]
            return [run.model_copy(deep=True) for run in runs]

    def clear(self) -> None:
        with self._lock:
            self._specs.clear()
            self._states.clear()
            self._drift.clear()
            self._runs.clear()


class PostgresReconcileStore:
    """A durable reconcile store backed by PostgreSQL (M26-04)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def replace_source_specs(
        self, source_id: str, specs: list[DesiredSpec], *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        for spec in specs:
            ctx.require(spec.tenant_id)
        with self._session.begin() as session:
            statement = delete(DesiredSpecRow).where(DesiredSpecRow.source_id == source_id)
            if not ctx.is_system:
                statement = statement.where(DesiredSpecRow.tenant_id == ctx.tenant_id)
            session.execute(statement)
            for spec in specs:
                session.add(self._spec_row(spec))

    @staticmethod
    def _spec_row(spec: DesiredSpec) -> DesiredSpecRow:
        return DesiredSpecRow(
            spec_id=spec.spec_id,
            tenant_id=spec.tenant_id,
            source_id=spec.source_id,
            source=spec.source.value,
            kind=spec.kind.value,
            name=spec.name,
            revision=spec.revision,
            content_hash=spec.content_hash,
            updated_at=spec.updated_at,
            payload=spec.model_dump(mode="json"),
        )

    def list_specs(
        self, source_id: str | None = None, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[DesiredSpec]:
        statement = select(DesiredSpecRow).order_by(DesiredSpecRow.kind, DesiredSpecRow.name)
        if source_id is not None:
            statement = statement.where(DesiredSpecRow.source_id == source_id)
        if not ctx.is_system:
            statement = statement.where(DesiredSpecRow.tenant_id == ctx.tenant_id)
        with self._session() as session:
            return [DesiredSpec.model_validate(row.payload) for row in session.scalars(statement)]

    def get_state(
        self, source_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> ReconcileState | None:
        with self._session() as session:
            row = session.get(ReconcileStateRow, (ctx.tenant_id, source_id))
            if row is None or not ctx.scopes(row.tenant_id):
                return None
            return ReconcileState.model_validate(row.payload)

    def save_state(
        self, state: ReconcileState, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(state.tenant_id)
        with self._session.begin() as session:
            row = session.get(ReconcileStateRow, (state.tenant_id, state.source_id))
            if row is None:
                session.add(
                    ReconcileStateRow(
                        tenant_id=state.tenant_id,
                        source_id=state.source_id,
                        source=state.source.value,
                        last_revision=state.last_revision,
                        last_observed_hash=state.last_observed_hash,
                        last_reconcile_at=state.last_reconcile_at,
                        status=state.status.value,
                        payload=state.model_dump(mode="json"),
                    )
                )
            else:
                row.source = state.source.value
                row.last_revision = state.last_revision
                row.last_observed_hash = state.last_observed_hash
                row.last_reconcile_at = state.last_reconcile_at
                row.status = state.status.value
                row.payload = state.model_dump(mode="json")

    def list_states(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> list[ReconcileState]:
        statement = select(ReconcileStateRow).order_by(ReconcileStateRow.source_id)
        if not ctx.is_system:
            statement = statement.where(ReconcileStateRow.tenant_id == ctx.tenant_id)
        with self._session() as session:
            return [
                ReconcileState.model_validate(row.payload) for row in session.scalars(statement)
            ]

    def add_drift(self, drift: DriftRecord, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(drift.tenant_id)
        with self._session.begin() as session:
            row = session.get(DriftRecordRow, drift.drift_id)
            if row is None:
                session.add(
                    DriftRecordRow(
                        drift_id=drift.drift_id,
                        tenant_id=drift.tenant_id,
                        object_ref=drift.object_ref,
                        field=drift.field,
                        detected_at=drift.detected_at,
                        resolution=drift.resolution.value,
                        reconcile_run_id=drift.reconcile_run_id,
                        payload=drift.model_dump(mode="json"),
                    )
                )
            else:
                if not ctx.scopes(row.tenant_id):
                    raise TenantScopeError(
                        ctx.tenant_id, f"cannot modify drift {drift.drift_id!r}"
                    )
                row.resolution = drift.resolution.value
                row.reconcile_run_id = drift.reconcile_run_id
                row.payload = drift.model_dump(mode="json")

    def save_drift(self, drift: DriftRecord, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        self.add_drift(drift, ctx=ctx)

    def list_drift(
        self,
        *,
        object_ref: str | None = None,
        reconcile_run_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[DriftRecord]:
        statement = select(DriftRecordRow).order_by(
            DriftRecordRow.detected_at, DriftRecordRow.drift_id
        )
        if object_ref is not None:
            statement = statement.where(DriftRecordRow.object_ref == object_ref)
        if reconcile_run_id is not None:
            statement = statement.where(DriftRecordRow.reconcile_run_id == reconcile_run_id)
        if not ctx.is_system:
            statement = statement.where(DriftRecordRow.tenant_id == ctx.tenant_id)
        with self._session() as session:
            return [DriftRecord.model_validate(row.payload) for row in session.scalars(statement)]

    def add_run(self, run: ReconcileRun, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(run.tenant_id)
        with self._session.begin() as session:
            if session.get(ReconcileRunRow, run.run_id) is not None:
                return
            session.add(self._run_row(run))

    @staticmethod
    def _run_row(run: ReconcileRun) -> ReconcileRunRow:
        return ReconcileRunRow(
            run_id=run.run_id,
            tenant_id=run.tenant_id,
            source_id=run.source_id,
            source=run.source.value,
            revision=run.revision,
            mode=run.mode.value,
            outcome=run.outcome.value,
            started_at=run.started_at,
            finished_at=run.finished_at,
            payload=run.model_dump(mode="json"),
        )

    def get_run(
        self, run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> ReconcileRun | None:
        with self._session() as session:
            row = session.get(ReconcileRunRow, run_id)
            if row is None or not ctx.scopes(row.tenant_id):
                return None
            return ReconcileRun.model_validate(row.payload)

    def list_runs(
        self,
        source_id: str | None = None,
        *,
        limit: int | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[ReconcileRun]:
        statement = select(ReconcileRunRow)
        if source_id is not None:
            statement = statement.where(ReconcileRunRow.source_id == source_id)
        if not ctx.is_system:
            statement = statement.where(ReconcileRunRow.tenant_id == ctx.tenant_id)
        if limit is not None:
            statement = statement.order_by(
                ReconcileRunRow.started_at.desc(), ReconcileRunRow.run_id.desc()
            ).limit(limit)
        else:
            statement = statement.order_by(
                ReconcileRunRow.started_at, ReconcileRunRow.run_id
            )
        with self._session() as session:
            rows = [ReconcileRun.model_validate(row.payload) for row in session.scalars(statement)]
        if limit is not None:
            rows.reverse()
        return rows

    def clear(self) -> None:
        with self._session.begin() as session:
            for table in (
                ReconcileRunRow,
                DriftRecordRow,
                ReconcileStateRow,
                DesiredSpecRow,
            ):
                session.execute(delete(table))


def build_reconcile_store(settings: Settings | None = None) -> ReconcileStore:
    """Build the configured reconcile store (in-memory by default)."""
    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresReconcileStore(create_engine_from_settings(resolved))
    return InMemoryReconcileStore()
