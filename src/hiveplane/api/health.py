"""Agent health, SLO, and burn API (M42)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from fastapi.responses import PlainTextResponse

from hiveplane.api.deps import (
    get_approval_service,
    get_health_service,
    get_plane_metrics,
    get_probe_service,
    get_tenant_context,
    require_permission,
)
from hiveplane.auth.models import OperatorIdentity, Permission
from hiveplane.health.analytics import ApprovalReport, approval_analytics
from hiveplane.health.models import BurnRate, BurnThroughAction, SloObjective, WorkloadHealth
from hiveplane.health.plane_metrics import PlaneMetrics
from hiveplane.health.service import HealthService
from hiveplane.policy.approvals import ApprovalService
from hiveplane.probes.models import ProbeResult, ProbeSchedule, ProbeSpec
from hiveplane.probes.service import ProbeService
from hiveplane.tenancy.context import TenantContext

router = APIRouter(tags=["health"])

HealthDep = Annotated[HealthService, Depends(get_health_service)]
ProbeDep = Annotated[ProbeService, Depends(get_probe_service)]
PlaneMetricsDep = Annotated[PlaneMetrics, Depends(get_plane_metrics)]
ApprovalDep = Annotated[ApprovalService, Depends(get_approval_service)]
TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]
FleetReader = Annotated[OperatorIdentity, Depends(require_permission(Permission.FLEET_READ))]


@router.get("/health", response_model=list[WorkloadHealth])
def fleet_health(service: HealthDep, ctx: TenantDep, _: FleetReader) -> list[WorkloadHealth]:
    """Return the health model for every workload in the acting tenant."""
    return service.fleet(ctx=ctx)


@router.get("/health/workloads/{workload}", response_model=WorkloadHealth)
def workload_health(
    workload: str, service: HealthDep, ctx: TenantDep, _: FleetReader
) -> WorkloadHealth:
    """Return the full health model for one workload."""
    return service.workload(workload, ctx=ctx)


@router.get("/health/workloads/{workload}/slo", response_model=list[SloObjective])
def workload_slo(
    workload: str, service: HealthDep, ctx: TenantDep, _: FleetReader
) -> list[SloObjective]:
    """Return a workload's SLO objectives and error-budget accounting."""
    return service.workload(workload, ctx=ctx).objectives


@router.get("/health/workloads/{workload}/burn", response_model=BurnRate)
def workload_burn(workload: str, service: HealthDep, ctx: TenantDep, _: FleetReader) -> BurnRate:
    """Return a workload's burn rate against its availability objective."""
    return service.burn_rate(workload, ctx=ctx)


@router.post("/health/workloads/{workload}/enforce", response_model=BurnThroughAction | None)
def enforce_burn_through(
    workload: str,
    service: HealthDep,
    ctx: TenantDep,
    _: Annotated[OperatorIdentity, Depends(require_permission(Permission.KILL_SWITCH))],
) -> BurnThroughAction | None:
    """Apply the burn-through action (quarantine) when a budget is exhausted."""
    return service.enforce(workload, ctx=ctx)


@router.get("/health/probes", response_model=list[ProbeResult])
def list_probes(
    service: ProbeDep, ctx: TenantDep, _: FleetReader, workload_id: str | None = None
) -> list[ProbeResult]:
    """List synthetic probe results, optionally filtered by workload."""
    if workload_id is not None:
        return service.list_results(workload_id, ctx=ctx)
    results: list[ProbeResult] = []
    for schedule in service.schedules(ctx=ctx):
        results.extend(service.list_results(schedule.workload_id, ctx=ctx))
    return results


@router.post("/health/probes/schedules", response_model=ProbeSchedule)
def schedule_probe(
    spec: ProbeSpec, service: ProbeDep, ctx: TenantDep, _: FleetReader
) -> ProbeSchedule:
    """Create or update the acting tenant's synthetic-probe schedule."""
    scoped = spec.model_copy(update={"tenant_id": ctx.tenant_id})
    return service.schedule(scoped, ctx=ctx)


@router.post("/health/probes/run", response_model=list[ProbeResult])
def run_due_probes(service: ProbeDep, ctx: TenantDep, _: FleetReader) -> list[ProbeResult]:
    """Run every matured synthetic probe now, without waiting for the ticker.

    An ops/testing convenience so a probe's verdict can be observed deterministically.
    """
    return service.run_due(ctx=ctx)


@router.get("/analytics/approvals", response_model=ApprovalReport)
def approvals_analytics(approvals: ApprovalDep, ctx: TenantDep, _: FleetReader) -> ApprovalReport:
    """Return per-approver latency, the bottleneck, and approve/deny trends."""
    return approval_analytics(approvals.list(ctx=ctx))


@router.get("/metrics", response_class=PlainTextResponse)
def plane_metrics(metrics: PlaneMetricsDep) -> str:
    """Expose the plane's own Prometheus metrics (no health-service dependency)."""
    return metrics.render()
