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
)
from hiveplane.health.analytics import ApprovalReport, approval_analytics
from hiveplane.health.models import BurnRate, BurnThroughAction, SloObjective, WorkloadHealth
from hiveplane.health.plane_metrics import PlaneMetrics
from hiveplane.health.service import HealthService
from hiveplane.policy.approvals import ApprovalService
from hiveplane.probes.models import ProbeResult
from hiveplane.probes.service import ProbeService

router = APIRouter(tags=["health"])

HealthDep = Annotated[HealthService, Depends(get_health_service)]
ProbeDep = Annotated[ProbeService, Depends(get_probe_service)]
PlaneMetricsDep = Annotated[PlaneMetrics, Depends(get_plane_metrics)]
ApprovalDep = Annotated[ApprovalService, Depends(get_approval_service)]


@router.get("/health", response_model=list[WorkloadHealth])
def fleet_health(service: HealthDep) -> list[WorkloadHealth]:
    """Return the health model for every workload."""
    return service.fleet()


@router.get("/health/workloads/{workload}", response_model=WorkloadHealth)
def workload_health(workload: str, service: HealthDep) -> WorkloadHealth:
    """Return the full health model for one workload."""
    return service.workload(workload)


@router.get("/health/workloads/{workload}/slo", response_model=list[SloObjective])
def workload_slo(workload: str, service: HealthDep) -> list[SloObjective]:
    """Return a workload's SLO objectives and error-budget accounting."""
    return service.workload(workload).objectives


@router.get("/health/workloads/{workload}/burn", response_model=BurnRate)
def workload_burn(workload: str, service: HealthDep) -> BurnRate:
    """Return a workload's burn rate against its availability objective."""
    return service.burn_rate(workload)


@router.post("/health/workloads/{workload}/enforce", response_model=BurnThroughAction | None)
def enforce_burn_through(workload: str, service: HealthDep) -> BurnThroughAction | None:
    """Apply the burn-through action (quarantine) when a budget is exhausted."""
    return service.enforce(workload)


@router.get("/health/probes", response_model=list[ProbeResult])
def list_probes(service: ProbeDep, workload_id: str | None = None) -> list[ProbeResult]:
    """List synthetic probe results, optionally filtered by workload."""
    if workload_id is not None:
        return service.list_results(workload_id)
    results: list[ProbeResult] = []
    for schedule in service.schedules():
        results.extend(service.list_results(schedule.workload_id))
    return results


@router.get("/analytics/approvals", response_model=ApprovalReport)
def approvals_analytics(approvals: ApprovalDep) -> ApprovalReport:
    """Return per-approver latency, the bottleneck, and approve/deny trends."""
    return approval_analytics(approvals.list())


@router.get("/metrics", response_class=PlainTextResponse)
def plane_metrics(metrics: PlaneMetricsDep) -> str:
    """Expose the plane's own Prometheus metrics (no health-service dependency)."""
    return metrics.render()
