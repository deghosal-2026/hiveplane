"""Agent health, SLO, and burn API (M42)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from hiveplane.api.deps import get_health_service
from hiveplane.health.models import BurnRate, BurnThroughAction, SloObjective, WorkloadHealth
from hiveplane.health.service import HealthService

router = APIRouter(tags=["health"])

HealthDep = Annotated[HealthService, Depends(get_health_service)]


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
