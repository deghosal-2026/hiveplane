"""Storage for workers, leases, heartbeats, and tokens (M46)."""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import Engine, delete, select

from hiveplane.config import Settings, get_settings
from hiveplane.fleet.workers import Worker, WorkerHeartbeat, WorkerLease
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import (
    WorkerHeartbeatRow,
    WorkerLeaseRow,
    WorkerRow,
    WorkerTokenRow,
)
from hiveplane.tenancy.context import DEFAULT_CONTEXT, TenantContext
from hiveplane.tenancy.errors import TenantScopeError
from hiveplane.worker.models import WorkerToken


class WorkerStore(Protocol):
    """Storage interface for the worker registry."""

    def save_worker(self, worker: Worker, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None: ...

    def get_worker(
        self, worker_id: str, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> Worker | None: ...

    def list_workers(
        self, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[Worker]: ...

    def list_all_workers(
        self, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[Worker]: ...

    def save_lease(self, lease: WorkerLease, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None: ...

    def get_lease(
        self, lease_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> WorkerLease | None: ...

    def list_leases(
        self,
        tenant_id: str,
        *,
        worker_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[WorkerLease]: ...

    def list_all_leases(
        self, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[WorkerLease]: ...

    def delete_lease(
        self, lease_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def claim_lease(
        self, lease_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> WorkerLease | None: ...

    def save_heartbeat(
        self, heartbeat: WorkerHeartbeat, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def list_heartbeats(
        self, worker_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[WorkerHeartbeat]: ...

    def save_token(
        self, token: WorkerToken, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def get_token(
        self, token_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> WorkerToken | None: ...

    def list_tokens(
        self, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[WorkerToken]: ...

    def clear(self) -> None: ...


class InMemoryWorkerStore:
    """A process-local worker registry store."""

    def __init__(self) -> None:
        self._workers: dict[tuple[str, str], Worker] = {}
        self._leases: dict[str, WorkerLease] = {}
        self._heartbeats: dict[str, list[WorkerHeartbeat]] = {}
        self._tokens: dict[str, WorkerToken] = {}

    def save_worker(self, worker: Worker, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(worker.tenant_id)
        self._workers[(worker.tenant_id, worker.worker_id)] = worker.model_copy(deep=True)

    def get_worker(
        self, worker_id: str, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> Worker | None:
        if not ctx.scopes(tenant_id):
            return None
        worker = self._workers.get((tenant_id, worker_id))
        return None if worker is None else worker.model_copy(deep=True)

    def list_workers(
        self, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[Worker]:
        if not ctx.scopes(tenant_id):
            return []
        return [
            worker.model_copy(deep=True)
            for (tenant, _), worker in sorted(self._workers.items())
            if tenant == tenant_id
        ]

    def list_all_workers(
        self, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[Worker]:
        if not ctx.is_system:
            raise TenantScopeError(ctx.tenant_id, "worker fleet view requires system context")
        return [
            worker.model_copy(deep=True)
            for _, worker in sorted(self._workers.items())
        ]

    def save_lease(self, lease: WorkerLease, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(lease.tenant_id)
        self._leases[lease.lease_id] = lease.model_copy(deep=True)

    def get_lease(
        self, lease_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> WorkerLease | None:
        lease = self._leases.get(lease_id)
        if lease is None or not ctx.scopes(lease.tenant_id):
            return None
        return lease.model_copy(deep=True)

    def list_all_leases(
        self, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[WorkerLease]:
        if not ctx.is_system:
            raise TenantScopeError(ctx.tenant_id, "worker fleet view requires system context")
        leases = [lease.model_copy(deep=True) for lease in self._leases.values()]
        leases.sort(key=lambda lease: lease.lease_id)
        return leases

    def list_leases(
        self,
        tenant_id: str,
        *,
        worker_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[WorkerLease]:
        if not ctx.scopes(tenant_id):
            return []
        leases = [
            lease.model_copy(deep=True)
            for lease in self._leases.values()
            if lease.tenant_id == tenant_id and (worker_id is None or lease.worker_id == worker_id)
        ]
        leases.sort(key=lambda lease: lease.lease_id)
        return leases

    def delete_lease(
        self, lease_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        lease = self._leases.get(lease_id)
        if lease is None or not ctx.scopes(lease.tenant_id):
            return
        del self._leases[lease_id]

    def claim_lease(
        self, lease_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> WorkerLease | None:
        lease = self._leases.get(lease_id)
        if lease is None or not ctx.scopes(lease.tenant_id):
            return None
        del self._leases[lease_id]
        return lease.model_copy(deep=True)

    def save_heartbeat(
        self, heartbeat: WorkerHeartbeat, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(heartbeat.tenant_id)
        self._heartbeats.setdefault(heartbeat.worker_id, []).append(
            heartbeat.model_copy(deep=True)
        )

    def list_heartbeats(
        self, worker_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[WorkerHeartbeat]:
        return [
            hb.model_copy(deep=True)
            for hb in self._heartbeats.get(worker_id, [])
            if ctx.scopes(hb.tenant_id)
        ]

    def save_token(
        self, token: WorkerToken, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(token.tenant_id)
        self._tokens[token.token_id] = token.model_copy(deep=True)

    def get_token(
        self, token_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> WorkerToken | None:
        token = self._tokens.get(token_id)
        if token is None or not ctx.scopes(token.tenant_id):
            return None
        return token.model_copy(deep=True)

    def list_tokens(
        self, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[WorkerToken]:
        if not ctx.scopes(tenant_id):
            return []
        return [
            token.model_copy(deep=True)
            for _, token in sorted(self._tokens.items())
            if token.tenant_id == tenant_id
        ]

    def clear(self) -> None:
        self._workers.clear()
        self._leases.clear()
        self._heartbeats.clear()
        self._tokens.clear()


class PostgresWorkerStore:
    """A durable worker registry store backed by PostgreSQL (M46)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def save_worker(self, worker: Worker, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(worker.tenant_id)
        with self._session.begin() as session:
            existing = session.get(WorkerRow, worker.worker_id)
            if (
                existing is not None
                and existing.tenant_id != worker.tenant_id
                and not ctx.is_system
            ):
                raise TenantScopeError(
                    worker.tenant_id,
                    f"worker {worker.worker_id!r} belongs to another tenant",
                )
            session.merge(
                WorkerRow(
                    worker_id=worker.worker_id,
                    tenant_id=worker.tenant_id,
                    state=worker.state.value,
                    version=worker.version,
                    registered_at=worker.registered_at,
                    last_seen_at=worker.last_seen_at,
                    payload=worker.model_dump(mode="json"),
                )
            )

    def get_worker(
        self, worker_id: str, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> Worker | None:
        if not ctx.scopes(tenant_id):
            return None
        with self._session() as session:
            row = session.get(WorkerRow, worker_id)
            if row is None or row.tenant_id != tenant_id:
                return None
            return Worker.model_validate(row.payload)

    def list_workers(
        self, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[Worker]:
        if not ctx.scopes(tenant_id):
            return []
        with self._session() as session:
            rows = session.scalars(
                select(WorkerRow)
                .where(WorkerRow.tenant_id == tenant_id)
                .order_by(WorkerRow.worker_id)
            ).all()
        return [Worker.model_validate(row.payload) for row in rows]

    def list_all_workers(
        self, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[Worker]:
        if not ctx.is_system:
            raise TenantScopeError(ctx.tenant_id, "worker fleet view requires system context")
        with self._session() as session:
            rows = session.scalars(select(WorkerRow).order_by(WorkerRow.worker_id)).all()
        return [Worker.model_validate(row.payload) for row in rows]

    def save_lease(self, lease: WorkerLease, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(lease.tenant_id)
        with self._session.begin() as session:
            session.merge(
                WorkerLeaseRow(
                    lease_id=lease.lease_id,
                    tenant_id=lease.tenant_id,
                    run_id=lease.run_id,
                    worker_id=lease.worker_id,
                    attempt=lease.attempt,
                    granted_at=lease.granted_at,
                    expires_at=lease.expires_at,
                    fencing_token=lease.fencing_token,
                    payload=lease.model_dump(mode="json"),
                )
            )

    def get_lease(
        self, lease_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> WorkerLease | None:
        with self._session() as session:
            row = session.get(WorkerLeaseRow, lease_id)
            if row is None or not ctx.scopes(row.tenant_id):
                return None
            return WorkerLease.model_validate(row.payload)

    def list_leases(
        self,
        tenant_id: str,
        *,
        worker_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[WorkerLease]:
        if not ctx.scopes(tenant_id):
            return []
        statement = select(WorkerLeaseRow).where(WorkerLeaseRow.tenant_id == tenant_id)
        if worker_id is not None:
            statement = statement.where(WorkerLeaseRow.worker_id == worker_id)
        with self._session() as session:
            rows = session.scalars(statement.order_by(WorkerLeaseRow.lease_id)).all()
        return [WorkerLease.model_validate(row.payload) for row in rows]

    def list_all_leases(
        self, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[WorkerLease]:
        if not ctx.is_system:
            raise TenantScopeError(ctx.tenant_id, "worker fleet view requires system context")
        with self._session() as session:
            rows = session.scalars(
                select(WorkerLeaseRow).order_by(WorkerLeaseRow.lease_id)
            ).all()
        return [WorkerLease.model_validate(row.payload) for row in rows]

    def delete_lease(
        self, lease_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        with self._session.begin() as session:
            row = session.get(WorkerLeaseRow, lease_id)
            if row is not None and ctx.scopes(row.tenant_id):
                session.delete(row)

    def claim_lease(
        self, lease_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> WorkerLease | None:
        """Atomically remove and return a lease so one reclaimer wins (M46-04)."""
        with self._session.begin() as session:
            row = session.get(WorkerLeaseRow, lease_id, with_for_update=True)
            if row is None or not ctx.scopes(row.tenant_id):
                return None
            lease = WorkerLease.model_validate(row.payload)
            session.delete(row)
            return lease

    def save_heartbeat(
        self, heartbeat: WorkerHeartbeat, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(heartbeat.tenant_id)
        with self._session.begin() as session:
            session.merge(
                WorkerHeartbeatRow(
                    heartbeat_id=heartbeat.heartbeat_id,
                    tenant_id=heartbeat.tenant_id,
                    worker_id=heartbeat.worker_id,
                    seen_at=heartbeat.seen_at,
                    status=heartbeat.status.value,
                    payload=heartbeat.model_dump(mode="json"),
                )
            )

    def list_heartbeats(
        self, worker_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[WorkerHeartbeat]:
        statement = select(WorkerHeartbeatRow).where(WorkerHeartbeatRow.worker_id == worker_id)
        if not ctx.is_system:
            statement = statement.where(WorkerHeartbeatRow.tenant_id == ctx.tenant_id)
        statement = statement.order_by(WorkerHeartbeatRow.seen_at)
        with self._session() as session:
            rows = session.scalars(statement).all()
        return [WorkerHeartbeat.model_validate(row.payload) for row in rows]

    def save_token(
        self, token: WorkerToken, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(token.tenant_id)
        with self._session.begin() as session:
            session.merge(
                WorkerTokenRow(
                    token_id=token.token_id,
                    tenant_id=token.tenant_id,
                    worker_id=token.worker_id,
                    issued_at=token.issued_at,
                    expires_at=token.expires_at,
                    revoked_at=token.revoked_at,
                    payload=token.model_dump(mode="json"),
                )
            )

    def get_token(
        self, token_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> WorkerToken | None:
        with self._session() as session:
            row = session.get(WorkerTokenRow, token_id)
            if row is None or not ctx.scopes(row.tenant_id):
                return None
            return WorkerToken.model_validate(row.payload)

    def list_tokens(
        self, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[WorkerToken]:
        if not ctx.scopes(tenant_id):
            return []
        with self._session() as session:
            rows = session.scalars(
                select(WorkerTokenRow)
                .where(WorkerTokenRow.tenant_id == tenant_id)
                .order_by(WorkerTokenRow.token_id)
            ).all()
        return [WorkerToken.model_validate(row.payload) for row in rows]

    def clear(self) -> None:
        with self._session.begin() as session:
            session.execute(delete(WorkerHeartbeatRow))
            session.execute(delete(WorkerLeaseRow))
            session.execute(delete(WorkerTokenRow))
            session.execute(delete(WorkerRow))


def build_worker_store(settings: Settings | None = None) -> WorkerStore:
    """Build the configured worker store (in-memory by default)."""
    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresWorkerStore(create_engine_from_settings(resolved))
    return InMemoryWorkerStore()
