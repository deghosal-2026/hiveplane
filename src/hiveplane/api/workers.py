"""Worker registration, heartbeat, lifecycle, and fleet view API (M46)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from hiveplane.api.deps import (
    get_leader_elector,
    get_tenant_context,
    get_worker_registry,
    require_permission,
)
from hiveplane.auth.models import OperatorIdentity, Permission
from hiveplane.ha.leader import LeaderElector
from hiveplane.tenancy.context import TenantContext
from hiveplane.worker.models import (
    RunAssignment,
    UnknownWorkerError,
    Worker,
    WorkerFleetEntry,
    WorkerHeartbeatRequest,
    WorkerIdentityError,
    WorkerNotReadyError,
    WorkerRegistrationRequest,
    WorkerToken,
    WorkerTokenIssued,
)
from hiveplane.worker.registry import WorkerRegistry

router = APIRouter(tags=["workers"])

RegistryDep = Annotated[WorkerRegistry, Depends(get_worker_registry)]
ElectorDep = Annotated[LeaderElector, Depends(get_leader_elector)]
FleetReader = Annotated[OperatorIdentity, Depends(require_permission(Permission.FLEET_READ))]
TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]


class EnrollRequest(BaseModel):
    """Request body to enroll a worker and issue its identity token."""

    model_config = ConfigDict(extra="forbid")

    worker_id: str = Field(min_length=1, max_length=64)


class GrantRequest(BaseModel):
    """Request body to lease a run to a worker."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    workload_id: str | None = None


@router.post("/workers/enroll", response_model=WorkerTokenIssued)
def enroll_worker(
    request: EnrollRequest, registry: RegistryDep, tenants: TenantDep
) -> WorkerTokenIssued:
    """Issue a signed worker identity token (plane-side enrollment)."""
    return registry.enroll(request.worker_id, tenant_id=tenants.tenant_id, ctx=tenants)


@router.post("/workers/register", response_model=Worker, status_code=status.HTTP_201_CREATED)
def register_worker(
    request: WorkerRegistrationRequest, registry: RegistryDep, tenants: TenantDep
) -> Worker:
    """Register a worker after verifying its identity token."""
    try:
        return registry.register(request, tenant_id=tenants.tenant_id, ctx=tenants)
    except WorkerIdentityError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc


@router.post("/workers/{worker_id}/heartbeat", response_model=Worker)
def heartbeat_worker(
    worker_id: str,
    request: WorkerHeartbeatRequest,
    registry: RegistryDep,
    tenants: TenantDep,
) -> Worker:
    """Record liveness/load and renew leases."""
    try:
        return registry.heartbeat(worker_id, request, tenant_id=tenants.tenant_id, ctx=tenants)
    except WorkerIdentityError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc
    except UnknownWorkerError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post("/workers/{worker_id}/leases")
def grant_lease(
    worker_id: str,
    request: GrantRequest,
    registry: RegistryDep,
    tenants: TenantDep,
) -> dict[str, object]:
    """Lease a run to a ready worker."""
    try:
        assignment = registry.grant(
            worker_id,
            request.run_id,
            workload_id=request.workload_id,
            tenant_id=tenants.tenant_id,
            ctx=tenants,
        )
    except UnknownWorkerError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except WorkerNotReadyError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return assignment.model_dump(mode="json")


@router.post("/workers/{worker_id}/drain", response_model=Worker)
def drain_worker(worker_id: str, registry: RegistryDep, tenants: TenantDep) -> Worker:
    """Begin draining a worker (finish in-flight work, no new leases)."""
    try:
        return registry.drain(worker_id, tenant_id=tenants.tenant_id, ctx=tenants)
    except UnknownWorkerError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.delete("/workers/{worker_id}", response_model=Worker)
def deregister_worker(worker_id: str, registry: RegistryDep, tenants: TenantDep) -> Worker:
    """Deregister a worker; its in-flight leases are requeued."""
    try:
        return registry.deregister(worker_id, tenant_id=tenants.tenant_id, ctx=tenants)
    except UnknownWorkerError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get("/workers", response_model=list[WorkerFleetEntry])
def list_workers(
    registry: RegistryDep,
    _: FleetReader,
    tenants: TenantDep,
) -> list[WorkerFleetEntry]:
    """Return the acting tenant's worker fleet view (liveness, load, leases)."""
    return registry.fleet(tenant_id=tenants.tenant_id, ctx=tenants)


@router.post("/workers/reclaim", response_model=list[RunAssignment])
def reclaim_workers(
    registry: RegistryDep,
    elector: ElectorDep,
    _: FleetReader,
    heartbeat_timeout_seconds: int | None = None,
) -> list[RunAssignment]:
    """Run one leader-gated lease-expiry/crash reclaim pass now (M46-03/04).

    Marks workers stale past the heartbeat timeout as unhealthy and requeues their
    leases to healthy workers. A no-op for standbys (only the leader reclaims).
    ``heartbeat_timeout_seconds`` overrides the timeout for this pass (forces a
    crash-recovery check without waiting the full timeout).
    """
    if elector.is_leader():
        elector.renew()
    elif not elector.acquire():
        return []
    registry.mark_unhealthy(timeout_s=heartbeat_timeout_seconds)
    return registry.reclaim()


@router.post("/workers/tokens/{token_id}/revoke", response_model=WorkerToken)
def revoke_worker_token(
    token_id: str, registry: RegistryDep, _: FleetReader, tenants: TenantDep
) -> WorkerToken:
    """Durably revoke a worker identity token (M46-05)."""
    token = registry.revoke_token(token_id, tenant_id=tenants.tenant_id, ctx=tenants)
    if token is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "worker token not found")
    return token
