"""Scheduling and queue visibility API (M47-08)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from hiveplane.api.deps import get_scheduler, get_tenant_context, require_permission
from hiveplane.auth.models import OperatorIdentity, Permission
from hiveplane.scheduler.models import QueueSnapshot
from hiveplane.scheduler.scheduler import Scheduler
from hiveplane.tenancy.context import TenantContext

router = APIRouter(tags=["scheduler"])

SchedulerDep = Annotated[Scheduler, Depends(get_scheduler)]
TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]
FleetReader = Annotated[OperatorIdentity, Depends(require_permission(Permission.FLEET_READ))]
KillSwitch = Annotated[
    OperatorIdentity, Depends(require_permission(Permission.KILL_SWITCH))
]


class FreezeRequest(BaseModel):
    """Request body to freeze admissions for a workload."""

    model_config = ConfigDict(extra="forbid")

    workload: str | None = None


@router.get("/queue", response_model=QueueSnapshot)
def queue_snapshot(
    scheduler: SchedulerDep, ctx: TenantDep, _: FleetReader
) -> QueueSnapshot:
    """Return queue depth, QoS/priority breakdown, waiting reasons, and load."""
    return scheduler.snapshot(ctx=ctx)


@router.post("/queue/freeze", response_model=QueueSnapshot)
def freeze_queue(
    request: FreezeRequest, scheduler: SchedulerDep, ctx: TenantDep, _: KillSwitch
) -> QueueSnapshot:
    """Freeze admissions for the acting tenant (optionally a workload)."""
    scheduler.freeze(tenant_id=ctx.tenant_id, workload=request.workload)
    return scheduler.snapshot(ctx=ctx)


@router.post("/queue/unfreeze", response_model=QueueSnapshot)
def unfreeze_queue(
    request: FreezeRequest, scheduler: SchedulerDep, ctx: TenantDep, _: KillSwitch
) -> QueueSnapshot:
    """Lift a queue freeze for the acting tenant (optionally a workload)."""
    scheduler.unfreeze(tenant_id=ctx.tenant_id, workload=request.workload)
    return scheduler.snapshot(ctx=ctx)
