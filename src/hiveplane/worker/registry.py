"""Worker registry: registration, leases, fencing, and crash reclaim (M46)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from uuid import uuid4

from hiveplane.fleet.workers import (
    Worker,
    WorkerHeartbeat,
    WorkerLease,
    WorkerState,
)
from hiveplane.tenancy.context import (
    DEFAULT_CONTEXT,
    SYSTEM_CONTEXT,
    TenantContext,
    context_for_run,
)
from hiveplane.worker.identity import WorkerIdentityService
from hiveplane.worker.models import (
    RunAssignment,
    StaleLeaseError,
    UnknownWorkerError,
    WorkerFleetEntry,
    WorkerHeartbeatRequest,
    WorkerIdentityError,
    WorkerNotReadyError,
    WorkerRegistrationRequest,
    WorkerReport,
    WorkerToken,
    WorkerTokenIssued,
)
from hiveplane.worker.store import WorkerStore


def new_lease_id() -> str:
    """Return a fresh opaque lease id."""
    return f"lease-{uuid4().hex[:20]}"


def new_heartbeat_id() -> str:
    """Return a fresh opaque heartbeat id."""
    return f"hb-{uuid4().hex[:20]}"


class WorkerRegistry:
    """Owns worker identity, liveness, leases, and crash-safe reassignment."""

    def __init__(
        self,
        store: WorkerStore,
        identity: WorkerIdentityService,
        *,
        clock: Callable[[], datetime] | None = None,
        heartbeat_timeout_s: int = 30,
        lease_ttl_s: int = 60,
        lease_id_factory: Callable[[], str] = new_lease_id,
        heartbeat_id_factory: Callable[[], str] = new_heartbeat_id,
    ) -> None:
        self._store = store
        self._identity = identity
        self._clock = clock or (lambda: datetime.now(UTC))
        self._heartbeat_timeout_s = heartbeat_timeout_s
        self._lease_ttl_s = lease_ttl_s
        self._lease_id_factory = lease_id_factory
        self._heartbeat_id_factory = heartbeat_id_factory

    # ------------------------------------------------------------------ #
    # Identity & registration
    # ------------------------------------------------------------------ #
    def _scope(self, tenant_id: str, ctx: TenantContext) -> TenantContext:
        """Return a scope for a trusted internal call against ``tenant_id``.

        A caller-supplied context that already scopes the tenant is used as-is;
        otherwise the explicit, trusted ``tenant_id`` argument defines the
        boundary (these methods are worker-plane plumbing, not request handlers).
        """
        return ctx if ctx.scopes(tenant_id) else context_for_run(tenant_id)

    def enroll(
        self,
        worker_id: str,
        *,
        tenant_id: str = "default",
        ttl_seconds: int | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> WorkerTokenIssued:
        """Issue a signed worker token (plane-side enrollment)."""
        issued = self._identity.issue(worker_id, tenant_id, ttl_seconds=ttl_seconds)
        self._store.save_token(issued.record, ctx=self._scope(tenant_id, ctx))
        return issued

    def revoke_token(
        self,
        token_id: str,
        *,
        tenant_id: str = "default",
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> WorkerToken | None:
        """Revoke a worker token durably; every verify path then refuses it (M46-05)."""
        scope = self._scope(tenant_id, ctx)
        token = self._store.get_token(token_id, ctx=scope)
        if token is None:
            return None
        token.revoked_at = self._clock()
        self._store.save_token(token, ctx=scope)
        self._identity.revoke(token_id)
        return token

    def register(
        self,
        request: WorkerRegistrationRequest,
        *,
        tenant_id: str = "default",
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> Worker:
        """Register a worker after verifying its identity token (M46-02)."""
        token = self._verify_token(request.token, request.worker_id)
        if token.tenant_id != tenant_id:
            raise WorkerIdentityError("worker token tenant mismatch")
        now = self._clock()
        worker = Worker(
            worker_id=request.worker_id,
            tenant_id=tenant_id,
            state=WorkerState.READY,
            capabilities=request.capabilities,
            version=request.version,
            registered_at=now,
            last_seen_at=now,
        )
        self._store.save_worker(worker, ctx=self._scope(tenant_id, ctx))
        return worker

    def heartbeat(
        self,
        worker_id: str,
        request: WorkerHeartbeatRequest,
        *,
        tenant_id: str = "default",
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> Worker:
        """Record liveness/load and renew active leases (M46-02)."""
        self._verify_token(request.token, worker_id)
        worker = self._require(worker_id, tenant_id, ctx)
        now = self._clock()
        worker.last_seen_at = now
        for lease_id in request.lease_ids:
            lease = self._store.get_lease(lease_id, ctx=self._scope(tenant_id, ctx))
            if lease is not None and lease.worker_id == worker_id:
                lease.expires_at = now + timedelta(seconds=self._lease_ttl_s)
                self._store.save_lease(lease, ctx=self._scope(tenant_id, ctx))
        if worker.state not in (WorkerState.DRAINING, WorkerState.MAINTENANCE):
            worker.state = (
                WorkerState.BUSY
                if request.running >= request.max_concurrency
                else WorkerState.READY
            )
        self._store.save_worker(worker, ctx=self._scope(tenant_id, ctx))
        self._store.save_heartbeat(
            WorkerHeartbeat(
                heartbeat_id=self._heartbeat_id_factory(),
                worker_id=worker_id,
                tenant_id=tenant_id,
                seen_at=now,
                status=worker.state,
            ),
            ctx=self._scope(tenant_id, ctx),
        )
        return worker

    # ------------------------------------------------------------------ #
    # Leases
    # ------------------------------------------------------------------ #
    def grant(
        self,
        worker_id: str,
        run_id: str,
        *,
        workload_id: str | None = None,
        tenant_id: str = "default",
        attempt: int = 1,
        fencing_token: int = 1,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> RunAssignment:
        """Lease a run to a ready worker (M46-03)."""
        worker = self._require(worker_id, tenant_id, ctx)
        if worker.state is not WorkerState.READY:
            raise WorkerNotReadyError(worker_id, worker.state)
        now = self._clock()
        lease = WorkerLease(
            lease_id=self._lease_id_factory(),
            tenant_id=tenant_id,
            run_id=run_id,
            worker_id=worker_id,
            workload_id=workload_id,
            attempt=attempt,
            granted_at=now,
            expires_at=now + timedelta(seconds=self._lease_ttl_s),
            fencing_token=fencing_token,
        )
        self._store.save_lease(lease, ctx=self._scope(tenant_id, ctx))
        active = self._store.list_leases(
            tenant_id, worker_id=worker_id, ctx=self._scope(tenant_id, ctx)
        )
        if len(active) >= max(worker.capabilities.capacity, 1):
            worker.state = WorkerState.BUSY
            self._store.save_worker(worker, ctx=self._scope(tenant_id, ctx))
        return RunAssignment(
            lease=lease,
            workload_id=workload_id or run_id,
            run_id=run_id,
        )

    def accept(self, lease_id: str, token: str) -> WorkerLease:
        """Accept a lease, verifying the worker identity (M46-05)."""
        lease = self._store.get_lease(lease_id, ctx=SYSTEM_CONTEXT)
        if lease is None:
            raise StaleLeaseError(lease_id, 0)
        self._verify_token(token, lease.worker_id)
        return lease

    def report(self, report: WorkerReport, *, token: str) -> Worker:
        """Apply a fenced report; a stale fence is rejected (zombie worker)."""
        self._verify_token(token, report.worker_id)
        lease = self._store.get_lease(report.lease_id, ctx=SYSTEM_CONTEXT)
        if lease is None or lease.fencing_token != report.fencing_token:
            raise StaleLeaseError(report.lease_id, report.fencing_token)
        scope = context_for_run(lease.tenant_id)
        self._store.delete_lease(lease.lease_id, ctx=scope)
        worker = self._require(report.worker_id, lease.tenant_id, scope)
        if worker.state not in (WorkerState.DRAINING, WorkerState.MAINTENANCE):
            worker.state = WorkerState.READY
        worker.last_seen_at = self._clock()
        self._store.save_worker(worker, ctx=scope)
        return worker

    def leases(
        self,
        worker_id: str,
        *,
        tenant_id: str = "default",
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[WorkerLease]:
        """Return a worker's active leases."""
        return self._store.list_leases(
            tenant_id, worker_id=worker_id, ctx=self._scope(tenant_id, ctx)
        )

    # ------------------------------------------------------------------ #
    # Lifecycle
    # ------------------------------------------------------------------ #
    def drain(
        self,
        worker_id: str,
        *,
        tenant_id: str = "default",
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> Worker:
        """Begin draining: finish in-flight work, take no new leases."""
        return self._set_state(worker_id, WorkerState.DRAINING, tenant_id, ctx)

    def maintenance(
        self,
        worker_id: str,
        *,
        tenant_id: str = "default",
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> Worker:
        """Drain plus exclude from scheduling."""
        return self._set_state(worker_id, WorkerState.MAINTENANCE, tenant_id, ctx)

    def resume(
        self,
        worker_id: str,
        *,
        tenant_id: str = "default",
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> Worker:
        """Return a draining/maintenance worker to ready."""
        return self._set_state(worker_id, WorkerState.READY, tenant_id, ctx)

    def deregister(
        self,
        worker_id: str,
        *,
        tenant_id: str = "default",
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> Worker:
        """Deregister a worker; its in-flight leases are reassigned (M46-06)."""
        worker = self._set_state(worker_id, WorkerState.DEREGISTERED, tenant_id, ctx)
        scope = self._scope(tenant_id, ctx)
        for lease in self._store.list_leases(tenant_id, worker_id=worker_id, ctx=scope):
            claimed = self._store.claim_lease(lease.lease_id, ctx=scope)
            if claimed is not None:
                self._reassign(claimed)
        return worker

    def mark_unhealthy(
        self, *, now: datetime | None = None, timeout_s: int | None = None
    ) -> list[str]:
        """Mark workers stale past the heartbeat timeout as unhealthy (M46-02).

        ``timeout_s`` overrides the configured heartbeat timeout for this pass
        (used by the reclaim endpoint to force a crash-recovery check).
        """
        moment = now or self._clock()
        timeout = timeout_s if timeout_s is not None else self._heartbeat_timeout_s
        cutoff = moment - timedelta(seconds=timeout)
        unhealthy: list[str] = []
        for worker in self._store.list_all_workers(ctx=SYSTEM_CONTEXT):
            if worker.state in (WorkerState.DEREGISTERED, WorkerState.MAINTENANCE):
                continue
            if worker.last_seen_at < cutoff:
                worker.state = WorkerState.UNHEALTHY
                self._store.save_worker(worker, ctx=SYSTEM_CONTEXT)
                unhealthy.append(worker.worker_id)
        return unhealthy

    def reclaim(self, *, now: datetime | None = None) -> list[RunAssignment]:
        """Requeue expired/unhealthy leases to healthy workers (M46-04)."""
        moment = now or self._clock()
        reassigned: list[RunAssignment] = []
        for lease in self._store.list_all_leases(ctx=SYSTEM_CONTEXT):
            scope = context_for_run(lease.tenant_id)
            worker = self._store.get_worker(lease.worker_id, lease.tenant_id, ctx=scope)
            expired = lease.expires_at <= moment
            crashed = worker is not None and worker.state is WorkerState.UNHEALTHY
            if not (expired or crashed):
                continue
            claimed = self._store.claim_lease(lease.lease_id, ctx=scope)
            if claimed is None:
                continue
            assignment = self._reassign(claimed)
            if assignment is not None:
                reassigned.append(assignment)
        return reassigned

    # ------------------------------------------------------------------ #
    # Fleet view
    # ------------------------------------------------------------------ #
    def fleet(
        self, *, tenant_id: str = "default", ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[WorkerFleetEntry]:
        """Return worker liveness, load, and active leases (M46-07)."""
        entries: list[WorkerFleetEntry] = []
        for worker in self._store.list_workers(tenant_id, ctx=self._scope(tenant_id, ctx)):
            leases = self._store.list_leases(
                tenant_id, worker_id=worker.worker_id, ctx=self._scope(tenant_id, ctx)
            )
            entries.append(
                WorkerFleetEntry(
                    worker=worker,
                    load=len(leases),
                    active_leases=[lease.lease_id for lease in leases],
                )
            )
        return entries

    # ------------------------------------------------------------------ #
    # Internals
    # ------------------------------------------------------------------ #
    def _verify_token(self, token: str, worker_id: str) -> WorkerToken:
        """Verify a token's signature/binding and its durable revocation state."""
        record = self._identity.verify(token, worker_id=worker_id)
        stored = self._store.get_token(record.token_id, ctx=SYSTEM_CONTEXT)
        if stored is not None and stored.revoked_at is not None:
            raise WorkerIdentityError("worker token revoked")
        return record

    def _require(
        self, worker_id: str, tenant_id: str, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> Worker:
        worker = self._store.get_worker(worker_id, tenant_id, ctx=self._scope(tenant_id, ctx))
        if worker is None:
            raise UnknownWorkerError(worker_id)
        return worker

    def _set_state(
        self,
        worker_id: str,
        state: WorkerState,
        tenant_id: str,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> Worker:
        worker = self._require(worker_id, tenant_id, ctx)
        worker.state = state
        worker.last_seen_at = self._clock()
        self._store.save_worker(worker, ctx=self._scope(tenant_id, ctx))
        return worker

    def _reassign(self, lease: WorkerLease) -> RunAssignment | None:
        """Reassign a removed lease to a ready worker with a bumped fence."""
        scope = context_for_run(lease.tenant_id)
        target = self._find_ready(lease.tenant_id, exclude=lease.worker_id, ctx=scope)
        if target is None:
            return None
        assignment = self.grant(
            target.worker_id,
            lease.run_id,
            workload_id=lease.workload_id,
            tenant_id=lease.tenant_id,
            attempt=lease.attempt + 1,
            fencing_token=lease.fencing_token + 1,
            ctx=scope,
        )
        return RunAssignment(
            lease=assignment.lease,
            workload_id=assignment.workload_id,
            run_id=assignment.run_id,
            reassigned_from=lease.worker_id,
        )

    def _find_ready(
        self,
        tenant_id: str,
        *,
        exclude: str,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> Worker | None:
        for worker in self._store.list_workers(tenant_id, ctx=self._scope(tenant_id, ctx)):
            if worker.worker_id == exclude or worker.state is not WorkerState.READY:
                continue
            leases = self._store.list_leases(
                tenant_id, worker_id=worker.worker_id, ctx=self._scope(tenant_id, ctx)
            )
            if len(leases) < max(worker.capabilities.capacity, 1):
                return worker
        return None
