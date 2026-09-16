"""Approval queue API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from hiveplane.api.deps import get_approval_service, get_run_service
from hiveplane.core.approval import ApprovalRecord, ApprovalStatus
from hiveplane.execution.models import InterventionAction
from hiveplane.execution.service import RunService
from hiveplane.policy.approvals import ApprovalService

router = APIRouter(tags=["approvals"])

ApprovalDep = Annotated[ApprovalService, Depends(get_approval_service)]
RunDep = Annotated[RunService, Depends(get_run_service)]


class ApprovalDecisionRequest(BaseModel):
    """Request body for approving or denying an approval."""

    model_config = ConfigDict(extra="forbid")

    operator: str = Field(min_length=1)
    reason: str | None = None


@router.get("/approvals", response_model=list[ApprovalRecord])
def list_approvals(
    service: ApprovalDep,
    status: ApprovalStatus | None = None,
    workload: str | None = None,
) -> list[ApprovalRecord]:
    """List approval requests."""
    return service.list(status=status, workload=workload)


@router.get("/approvals/{approval_id}", response_model=ApprovalRecord)
def get_approval(approval_id: str, service: ApprovalDep) -> ApprovalRecord:
    """Return an approval request."""
    return service.get(approval_id)


@router.post("/approvals/{approval_id}/approve", response_model=ApprovalRecord)
def approve(
    approval_id: str, payload: ApprovalDecisionRequest, service: ApprovalDep, runs: RunDep
) -> ApprovalRecord:
    """Approve a request and resume the paused run."""
    record = service.decide(
        approval_id,
        status=ApprovalStatus.APPROVED,
        operator=payload.operator,
        reason=payload.reason,
    )
    runs.intervene(record.run_id, InterventionAction.RESUME, actor=payload.operator)
    return record


@router.post("/approvals/{approval_id}/deny", response_model=ApprovalRecord)
def deny(
    approval_id: str, payload: ApprovalDecisionRequest, service: ApprovalDep, runs: RunDep
) -> ApprovalRecord:
    """Deny a request and fail the paused run."""
    record = service.decide(
        approval_id,
        status=ApprovalStatus.DENIED,
        operator=payload.operator,
        reason=payload.reason,
    )
    runs.fail(record.run_id, actor=payload.operator, reason=payload.reason or "approval denied")
    return record
