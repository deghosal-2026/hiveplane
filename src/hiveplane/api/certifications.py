"""Certification API: run, inspect, and compare certifications (M7, #25)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict

from hiveplane.api.deps import get_certification_coordinator
from hiveplane.certification.models import (
    CertificationRecord,
    CertificationStatus,
    RegressionDiff,
    TargetContext,
)
from hiveplane.certification.workflow import CertificationCoordinator

router = APIRouter(tags=["certifications"])

CoordinatorDep = Annotated[CertificationCoordinator, Depends(get_certification_coordinator)]


class CertificationRequest(BaseModel):
    """A request to run a workload's benchmark and certify the result."""

    model_config = ConfigDict(extra="forbid")

    workload: str
    target_context: TargetContext = TargetContext.STAGING
    corpus: str | None = None
    production_runs_survived: int = 0


@router.post(
    "/certifications",
    response_model=CertificationRecord,
    status_code=status.HTTP_201_CREATED,
)
def start_certification(
    payload: CertificationRequest, coordinator: CoordinatorDep
) -> CertificationRecord:
    """Run a workload's benchmark corpus and record the certification."""
    return coordinator.certify(
        payload.workload,
        target_context=payload.target_context,
        corpus_ref=payload.corpus,
        production_runs_survived=payload.production_runs_survived,
    )


@router.get("/certifications", response_model=list[CertificationRecord])
def list_certifications(
    coordinator: CoordinatorDep,
    workload: str | None = None,
    status: CertificationStatus | None = None,
    limit: int | None = None,
    offset: int = 0,
) -> list[CertificationRecord]:
    """List certification records, optionally filtered."""
    return coordinator.list(workload=workload, status=status, limit=limit, offset=offset)


@router.get("/certifications/compare/{before_id}/{after_id}", response_model=RegressionDiff)
def compare_certifications(
    before_id: str, after_id: str, coordinator: CoordinatorDep
) -> RegressionDiff:
    """Return the task-level regression diff between two certifications."""
    return coordinator.compare(before_id, after_id)


@router.get("/certifications/{certification_id}", response_model=CertificationRecord)
def get_certification(
    certification_id: str, coordinator: CoordinatorDep
) -> CertificationRecord:
    """Return a certification record by id."""
    return coordinator.get(certification_id)
