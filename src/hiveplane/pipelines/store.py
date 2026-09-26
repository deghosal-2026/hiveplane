"""Pipeline storage: specs, parent runs, and node runs (M29-02).

Two implementations share one protocol: an in-memory store for tests and a
Postgres store for durability. Specs persist in the ``pipelines`` table and
execution records in ``pipeline_run_headers`` / ``pipeline_node_runs``; every
read and write is filtered by the acting tenant context.
"""

from __future__ import annotations

import threading
from datetime import UTC, datetime
from typing import Protocol

from sqlalchemy import Engine, delete, select

from hiveplane.config import Settings, get_settings
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import (
    PipelineNodeRunRow,
    PipelineRow,
    PipelineRunHeaderRow,
)
from hiveplane.pipelines.models import PipelineNodeRun, PipelineRunHeader
from hiveplane.pipelines.spec import PipelineSpec
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext, TenantScopeError


class PipelineStore(Protocol):
    """Storage interface for pipeline specs and execution records."""

    def save_spec(self, spec: PipelineSpec, *, ctx: TenantContext = ...) -> None: ...

    def get_spec(self, pipeline_id: str, *, ctx: TenantContext = ...) -> PipelineSpec | None: ...

    def list_specs(self, *, ctx: TenantContext = ...) -> list[PipelineSpec]: ...

    def save_run(self, header: PipelineRunHeader, *, ctx: TenantContext = ...) -> None: ...

    def get_run(
        self, pipeline_run_id: str, *, ctx: TenantContext = ...
    ) -> PipelineRunHeader | None: ...

    def list_runs(
        self, pipeline_id: str | None = None, *, ctx: TenantContext = ...
    ) -> list[PipelineRunHeader]: ...

    def save_node_run(self, node: PipelineNodeRun, *, ctx: TenantContext = ...) -> None: ...

    def get_node_run(
        self, pipeline_run_id: str, node_id: str, attempt: int, *, ctx: TenantContext = ...
    ) -> PipelineNodeRun | None: ...

    def list_node_runs(
        self, pipeline_run_id: str, *, ctx: TenantContext = ...
    ) -> list[PipelineNodeRun]: ...

    def clear(self) -> None: ...


class InMemoryPipelineStore:
    """A process-local, thread-safe pipeline store."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._specs: dict[str, tuple[PipelineSpec, str]] = {}
        self._runs: dict[str, tuple[PipelineRunHeader, str]] = {}
        self._nodes: dict[tuple[str, str, int], PipelineNodeRun] = {}

    def save_spec(self, spec: PipelineSpec, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(spec.tenant_id)
        with self._lock:
            self._specs[spec.id] = (spec.model_copy(deep=True), spec.tenant_id)

    def get_spec(
        self, pipeline_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> PipelineSpec | None:
        with self._lock:
            entry = self._specs.get(pipeline_id)
            if entry is None or not ctx.scopes(entry[1]):
                return None
            return entry[0].model_copy(deep=True)

    def list_specs(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> list[PipelineSpec]:
        with self._lock:
            specs = [spec for spec, tenant in self._specs.values() if ctx.scopes(tenant)]
            specs.sort(key=lambda spec: spec.id)
            return [spec.model_copy(deep=True) for spec in specs]

    def save_run(self, header: PipelineRunHeader, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(header.tenant_id)
        with self._lock:
            self._runs[header.pipeline_run_id] = (header.model_copy(deep=True), header.tenant_id)

    def get_run(
        self, pipeline_run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> PipelineRunHeader | None:
        with self._lock:
            entry = self._runs.get(pipeline_run_id)
            if entry is None or not ctx.scopes(entry[1]):
                return None
            return entry[0].model_copy(deep=True)

    def list_runs(
        self, pipeline_id: str | None = None, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[PipelineRunHeader]:
        with self._lock:
            runs = [
                header
                for header, tenant in self._runs.values()
                if ctx.scopes(tenant)
                and (pipeline_id is None or header.pipeline_id == pipeline_id)
            ]
            runs.sort(key=lambda header: (header.started_at, header.pipeline_run_id))
            return [header.model_copy(deep=True) for header in runs]

    def save_node_run(self, node: PipelineNodeRun, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(node.tenant_id)
        with self._lock:
            key = (node.pipeline_run_id, node.node_id, node.attempt)
            self._nodes[key] = node.model_copy(deep=True)

    def get_node_run(
        self,
        pipeline_run_id: str,
        node_id: str,
        attempt: int,
        *,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> PipelineNodeRun | None:
        with self._lock:
            node = self._nodes.get((pipeline_run_id, node_id, attempt))
            if node is None or not ctx.scopes(node.tenant_id):
                return None
            return node.model_copy(deep=True)

    def list_node_runs(
        self, pipeline_run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[PipelineNodeRun]:
        with self._lock:
            nodes = [
                node
                for node in self._nodes.values()
                if node.pipeline_run_id == pipeline_run_id and ctx.scopes(node.tenant_id)
            ]
            nodes.sort(key=lambda node: (node.node_id, node.attempt))
            return [node.model_copy(deep=True) for node in nodes]

    def clear(self) -> None:
        with self._lock:
            self._specs.clear()
            self._runs.clear()
            self._nodes.clear()


class PostgresPipelineStore:
    """A durable pipeline store backed by PostgreSQL (M29-02)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def save_spec(self, spec: PipelineSpec, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(spec.tenant_id)
        with self._session.begin() as session:
            row = session.get(PipelineRow, spec.id)
            created_at = spec.created_at or datetime.now(UTC)
            if row is None:
                session.add(
                    PipelineRow(
                        pipeline_id=spec.id,
                        tenant_id=spec.tenant_id,
                        name=spec.name,
                        version=spec.version,
                        created_at=created_at,
                        payload=spec.model_dump(mode="json"),
                    )
                )
            else:
                if not ctx.scopes(row.tenant_id):
                    raise TenantScopeError(
                        ctx.tenant_id, f"cannot modify pipeline {spec.id!r}"
                    )
                row.name = spec.name
                row.version = spec.version
                row.payload = spec.model_dump(mode="json")

    def get_spec(
        self, pipeline_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> PipelineSpec | None:
        with self._session() as session:
            row = session.get(PipelineRow, pipeline_id)
            if row is None or not ctx.scopes(row.tenant_id):
                return None
            return PipelineSpec.model_validate(row.payload)

    def list_specs(self, *, ctx: TenantContext = DEFAULT_CONTEXT) -> list[PipelineSpec]:
        statement = select(PipelineRow).order_by(PipelineRow.pipeline_id)
        if not ctx.is_system:
            statement = statement.where(PipelineRow.tenant_id == ctx.tenant_id)
        with self._session() as session:
            return [PipelineSpec.model_validate(row.payload) for row in session.scalars(statement)]

    def save_run(self, header: PipelineRunHeader, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(header.tenant_id)
        with self._session.begin() as session:
            row = session.get(PipelineRunHeaderRow, header.pipeline_run_id)
            if row is None:
                session.add(
                    PipelineRunHeaderRow(
                        pipeline_run_id=header.pipeline_run_id,
                        tenant_id=header.tenant_id,
                        pipeline_id=header.pipeline_id,
                        version=header.version,
                        state=header.state.value,
                        budget_usd=header.budget_usd,
                        spent_usd=header.spent_usd,
                        parent_run_id=header.parent_run_id,
                        started_at=header.started_at,
                        finished_at=header.finished_at,
                        payload=header.model_dump(mode="json"),
                    )
                )
            else:
                if not ctx.scopes(row.tenant_id):
                    raise TenantScopeError(
                        ctx.tenant_id, f"cannot modify pipeline run {header.pipeline_run_id!r}"
                    )
                row.state = header.state.value
                row.budget_usd = header.budget_usd
                row.spent_usd = header.spent_usd
                row.finished_at = header.finished_at
                row.payload = header.model_dump(mode="json")

    def get_run(
        self, pipeline_run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> PipelineRunHeader | None:
        with self._session() as session:
            row = session.get(PipelineRunHeaderRow, pipeline_run_id)
            if row is None or not ctx.scopes(row.tenant_id):
                return None
            return PipelineRunHeader.model_validate(row.payload)

    def list_runs(
        self, pipeline_id: str | None = None, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[PipelineRunHeader]:
        statement = select(PipelineRunHeaderRow).order_by(
            PipelineRunHeaderRow.started_at, PipelineRunHeaderRow.pipeline_run_id
        )
        if pipeline_id is not None:
            statement = statement.where(PipelineRunHeaderRow.pipeline_id == pipeline_id)
        if not ctx.is_system:
            statement = statement.where(PipelineRunHeaderRow.tenant_id == ctx.tenant_id)
        with self._session() as session:
            return [
                PipelineRunHeader.model_validate(row.payload)
                for row in session.scalars(statement)
            ]

    def save_node_run(self, node: PipelineNodeRun, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(node.tenant_id)
        with self._session.begin() as session:
            row = session.get(
                PipelineNodeRunRow, (node.pipeline_run_id, node.node_id, node.attempt)
            )
            if row is None:
                session.add(
                    PipelineNodeRunRow(
                        pipeline_run_id=node.pipeline_run_id,
                        node_id=node.node_id,
                        attempt=node.attempt,
                        tenant_id=node.tenant_id,
                        child_run_id=node.child_run_id,
                        status=node.status.value,
                        cost_usd=node.cost_usd,
                        payload=node.model_dump(mode="json"),
                    )
                )
            else:
                row.child_run_id = node.child_run_id
                row.status = node.status.value
                row.cost_usd = node.cost_usd
                row.payload = node.model_dump(mode="json")

    def get_node_run(
        self,
        pipeline_run_id: str,
        node_id: str,
        attempt: int,
        *,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> PipelineNodeRun | None:
        with self._session() as session:
            row = session.get(PipelineNodeRunRow, (pipeline_run_id, node_id, attempt))
            if row is None or not ctx.scopes(row.tenant_id):
                return None
            return PipelineNodeRun.model_validate(row.payload)

    def list_node_runs(
        self, pipeline_run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[PipelineNodeRun]:
        statement = (
            select(PipelineNodeRunRow)
            .where(PipelineNodeRunRow.pipeline_run_id == pipeline_run_id)
            .order_by(PipelineNodeRunRow.node_id, PipelineNodeRunRow.attempt)
        )
        if not ctx.is_system:
            statement = statement.where(PipelineNodeRunRow.tenant_id == ctx.tenant_id)
        with self._session() as session:
            return [
                PipelineNodeRun.model_validate(row.payload)
                for row in session.scalars(statement)
            ]

    def clear(self) -> None:
        with self._session.begin() as session:
            for table in (PipelineNodeRunRow, PipelineRunHeaderRow, PipelineRow):
                session.execute(delete(table))


def build_pipeline_store(settings: Settings | None = None) -> PipelineStore:
    """Build the configured pipeline store (in-memory by default)."""
    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresPipelineStore(create_engine_from_settings(resolved))
    return InMemoryPipelineStore()
