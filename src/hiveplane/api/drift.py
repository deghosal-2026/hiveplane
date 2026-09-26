"""Drift, quarantine, and reinstatement API (M34)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from hiveplane.certification.models import EvalSummary, TargetContext
from hiveplane.drift.errors import (
    DriftNotConfiguredError,
    QuarantineNotFoundError,
    ReinstatementRefusedError,
)
from hiveplane.drift.models import (
    CertificationExpiry,
    DriftAssessment,
    DriftSchedule,
    DueWorkload,
    QuarantineRecord,
    QuarantineStatus,
)
from hiveplane.drift.monitor import DriftMonitor
from hiveplane.drift.quarantine import QuarantineService
from hiveplane.drift.reinstatement import ReinstatementService
from hiveplane.drift.scheduler import DriftScheduler
from hiveplane.registry.errors import WorkloadNotFoundError
from hiveplane.tenancy import TenantContext

from .deps import (
    get_drift_monitor,
    get_drift_scheduler,
    get_quarantine_service,
    get_reinstatement_service,
    get_tenant_context,
)

router = APIRouter(tags=["drift"])

SchedulerDep = Annotated[DriftScheduler, Depends(get_drift_scheduler)]
MonitorDep = Annotated[DriftMonitor, Depends(get_drift_monitor)]
QuarantineDep = Annotated[QuarantineService, Depends(get_quarantine_service)]
ReinstatementDep = Annotated[ReinstatementService, Depends(get_reinstatement_service)]
TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]


class DriftAssessRequest(BaseModel):
    """Assess a workload's current performance against its certified baseline."""

    model_config = ConfigDict(extra="forbid")

    workload: str = Field(min_length=1)
    current: EvalSummary
    baseline_attestation_id: str | None = None


class DriftProbeRequest(BaseModel):
    """Run a fresh benchmark for a workload and assess it for drift."""

    model_config = ConfigDict(extra="forbid")

    workload: str = Field(min_length=1)
    target_context: TargetContext = TargetContext.STAGING
    corpus: str | None = None
    model_identity: str | None = None


class QuarantineRequest(BaseModel):
    """Quarantine a workload (manual or automated)."""

    model_config = ConfigDict(extra="forbid")

    workload: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    operator: str = Field(default="operator", min_length=1)


class ReinstateRequest(BaseModel):
    """Reinstate a quarantined workload after a fresh passing certification."""

    model_config = ConfigDict(extra="forbid")

    operator: str = Field(default="operator", min_length=1)
    corpus: str | None = None
    model_identity: str | None = None


@router.get("/drift/due", response_model=list[DueWorkload])
def list_due(scheduler: SchedulerDep) -> list[DueWorkload]:
    """List workloads whose re-certification window is due or expired."""
    return scheduler.due()


@router.get("/drift/schedules", response_model=list[DriftSchedule])
def list_schedules(scheduler: SchedulerDep) -> list[DriftSchedule]:
    """List the computed re-certification schedule per workload."""
    return scheduler.schedules()


@router.get("/drift/expiries", response_model=list[CertificationExpiry])
def list_expiries(scheduler: SchedulerDep) -> list[CertificationExpiry]:
    """List certification expiry/renewal states, most urgent first."""
    return scheduler.expiries()


@router.post("/drift/assess", response_model=DriftAssessment)
def assess_drift(
    payload: DriftAssessRequest, monitor: MonitorDep, ctx: TenantDep
) -> DriftAssessment:
    """Assess a workload's current performance and quarantine on confirmed drift."""
    try:
        return monitor.evaluate(
            payload.workload,
            payload.current,
            baseline_attestation_id=payload.baseline_attestation_id,
            ctx=ctx,
        )
    except WorkloadNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except DriftNotConfiguredError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.post("/drift/probe", response_model=DriftAssessment)
def probe_drift(
    payload: DriftProbeRequest, monitor: MonitorDep, ctx: TenantDep
) -> DriftAssessment:
    """Run a fresh benchmark for a workload and assess it for drift."""
    try:
        return monitor.probe_and_evaluate(
            payload.workload,
            target_context=payload.target_context,
            corpus_ref=payload.corpus,
            model_identity=payload.model_identity,
            ctx=ctx,
        )
    except WorkloadNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except DriftNotConfiguredError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get("/quarantines", response_model=list[QuarantineRecord])
def list_quarantines(
    service: QuarantineDep,
    ctx: TenantDep,
    workload: str | None = None,
    status_filter: QuarantineStatus | None = None,
) -> list[QuarantineRecord]:
    """List quarantine history, optionally filtered."""
    return service.list(workload=workload, status=status_filter, ctx=ctx)


@router.post(
    "/quarantines", response_model=QuarantineRecord, status_code=status.HTTP_201_CREATED
)
def quarantine(
    payload: QuarantineRequest, service: QuarantineDep, ctx: TenantDep
) -> QuarantineRecord:
    """Quarantine a workload and revoke its production admission."""
    try:
        return service.quarantine(
            payload.workload,
            reason=payload.reason,
            actor=payload.operator,
            ctx=ctx,
        )
    except WorkloadNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.get("/quarantines/{quarantine_id}", response_model=QuarantineRecord)
def get_quarantine(
    quarantine_id: str, service: QuarantineDep, ctx: TenantDep
) -> QuarantineRecord:
    """Return one quarantine record."""
    record = service.get(quarantine_id, ctx=ctx)
    if record is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"quarantine {quarantine_id!r} not found"
        )
    return record


@router.post("/quarantines/{quarantine_id}/reinstate", response_model=QuarantineRecord)
def reinstate(
    quarantine_id: str,
    payload: ReinstateRequest,
    quarantine_service: QuarantineDep,
    reinstatement: ReinstatementDep,
    ctx: TenantDep,
) -> QuarantineRecord:
    """Re-certify a quarantined workload and resume production admission."""
    record = quarantine_service.get(quarantine_id, ctx=ctx)
    if record is None:
        raise HTTPException(
            status.HTTP_404_NOT_FOUND, f"quarantine {quarantine_id!r} not found"
        )
    try:
        return reinstatement.reinstate(
            record.workload,
            operator=payload.operator,
            corpus_ref=payload.corpus,
            model_identity=payload.model_identity,
            ctx=ctx,
        )
    except QuarantineNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    except ReinstatementRefusedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except WorkloadNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
