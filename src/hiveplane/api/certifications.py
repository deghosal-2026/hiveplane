"""Certification API: run, inspect, and compare certifications (M7, #25)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict

from hiveplane.api.deps import (
    get_certification_coordinator,
    get_tenant_context,
    require_permission,
)
from hiveplane.auth.models import OperatorIdentity, Permission
from hiveplane.certification.errors import CertificationNotFoundError
from hiveplane.certification.models import (
    CertificationRecord,
    CertificationStatus,
    RegressionDiff,
    TargetContext,
)
from hiveplane.certification.workflow import CertificationCoordinator
from hiveplane.tenancy.context import TenantContext

router = APIRouter(tags=["certifications"])

CoordinatorDep = Annotated[CertificationCoordinator, Depends(get_certification_coordinator)]
TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]
FleetReader = Annotated[OperatorIdentity, Depends(require_permission(Permission.FLEET_READ))]
Certifier = Annotated[
    OperatorIdentity, Depends(require_permission(Permission.CERTIFY_MANAGE))
]


class CertificationRequest(BaseModel):
    """A request to run a workload's benchmark and certify the result."""

    model_config = ConfigDict(extra="forbid")

    workload: str
    target_context: TargetContext = TargetContext.STAGING
    corpus: str | None = None
    model_identity: str | None = None


@router.post(
    "/certifications",
    response_model=CertificationRecord,
    status_code=status.HTTP_201_CREATED,
)
def start_certification(
    payload: CertificationRequest,
    coordinator: CoordinatorDep,
    tenants: TenantDep,
    _: Certifier,
) -> CertificationRecord:
    """Run a workload's benchmark corpus and record the certification."""
    return coordinator.certify(
        payload.workload,
        target_context=payload.target_context,
        corpus_ref=payload.corpus,
        model_identity=payload.model_identity,
        ctx=tenants,
    )


@router.get("/certifications", response_model=list[CertificationRecord])
def list_certifications(
    coordinator: CoordinatorDep,
    tenants: TenantDep,
    _: FleetReader,
    workload: str | None = None,
    status: CertificationStatus | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[CertificationRecord]:
    """List certification records, optionally filtered."""
    return coordinator.list(
        workload=workload, status=status, limit=limit, offset=offset, ctx=tenants
    )


@router.get("/certifications/compare/{before_id}/{after_id}", response_model=RegressionDiff)
def compare_certifications(
    before_id: str,
    after_id: str,
    coordinator: CoordinatorDep,
    tenants: TenantDep,
    _: FleetReader,
) -> RegressionDiff:
    """Return the task-level regression diff between two certifications."""
    return coordinator.compare(before_id, after_id, ctx=tenants)


@router.get(
    "/certifications/compare-baseline/{after_id}", response_model=RegressionDiff
)
def compare_to_baseline(
    after_id: str,
    coordinator: CoordinatorDep,
    workload: str,
    tenants: TenantDep,
    _: FleetReader,
    baseline: str | None = None,
) -> RegressionDiff:
    """Compare a certification to its baseline (last certified unless specified)."""
    try:
        return coordinator.compare_to_baseline(
            workload, after_id, baseline_id=baseline, ctx=tenants
        )
    except CertificationNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get("/certifications/{record_id}", response_model=CertificationRecord)
def get_certification(
    record_id: str, coordinator: CoordinatorDep, tenants: TenantDep, _: FleetReader
) -> CertificationRecord:
    """Return a certification record by record id."""
    return coordinator.get(record_id, ctx=tenants)
