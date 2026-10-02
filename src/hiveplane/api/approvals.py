"""Approval queue API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from hiveplane.api.deps import (
    get_approval_service,
    get_run_service,
    get_tenant_context,
    require_permission,
)
from hiveplane.auth.models import OperatorIdentity, Permission
from hiveplane.core.approval import ApprovalRecord, ApprovalStatus
from hiveplane.core.run import RunState
from hiveplane.delivery.models import InvalidApprovalTokenError
from hiveplane.execution.models import InterventionAction
from hiveplane.execution.service import RunService
from hiveplane.policy.approvals import ApprovalService
from hiveplane.tenancy import TenantContext

router = APIRouter(tags=["approvals"])

ApprovalDep = Annotated[ApprovalService, Depends(get_approval_service)]
RunDep = Annotated[RunService, Depends(get_run_service)]
TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]
FleetReader = Annotated[
    OperatorIdentity, Depends(require_permission(Permission.FLEET_READ))
]


class ApprovalDecisionRequest(BaseModel):
    """Request body for approving or denying an approval.

    The acting operator is always derived from the authenticated principal, so
    any client-supplied ``operator`` is accepted for compatibility and ignored.
    """

    model_config = ConfigDict(extra="forbid")

    operator: str | None = None
    reason: str | None = None


class ApprovalCommentRequest(BaseModel):
    """Request body for commenting on an approval.

    The comment author is the authenticated principal; ``author`` is ignored.
    """

    model_config = ConfigDict(extra="forbid")

    author: str | None = None
    text: str = Field(min_length=1)


class DelegationRequest(BaseModel):
    """Request body for delegating an approval to another operator.

    The delegating operator is the authenticated principal; ``operator`` is ignored.
    """

    model_config = ConfigDict(extra="forbid")

    assignee: str = Field(min_length=1)
    operator: str | None = None


@router.get("/approvals", response_model=list[ApprovalRecord])
def list_approvals(
    service: ApprovalDep,
    tenants: TenantDep,
    _: FleetReader,
    status: ApprovalStatus | None = None,
    workload: str | None = None,
) -> list[ApprovalRecord]:
    """List approval requests."""
    return service.list(status=status, workload=workload, ctx=tenants)


@router.get("/approvals/{approval_id}", response_model=ApprovalRecord)
def get_approval(
    approval_id: str, service: ApprovalDep, tenants: TenantDep, _: FleetReader
) -> ApprovalRecord:
    """Return an approval request."""
    return service.get(approval_id, ctx=tenants)


@router.post("/approvals/{approval_id}/approve", response_model=ApprovalRecord)
def approve(
    approval_id: str,
    payload: ApprovalDecisionRequest,
    service: ApprovalDep,
    runs: RunDep,
    ctx: TenantDep,
    principal: Annotated[OperatorIdentity, Depends(require_permission(Permission.APPROVE))],
) -> ApprovalRecord:
    """Approve a request and resume the paused run."""
    return apply_approval_decision(
        service,
        runs,
        approval_id,
        decision="approve",
        operator=principal.operator_id,
        reason=payload.reason,
        ctx=ctx,
    )


@router.post("/approvals/{approval_id}/deny", response_model=ApprovalRecord)
def deny(
    approval_id: str,
    payload: ApprovalDecisionRequest,
    service: ApprovalDep,
    runs: RunDep,
    ctx: TenantDep,
    principal: Annotated[OperatorIdentity, Depends(require_permission(Permission.APPROVE))],
) -> ApprovalRecord:
    """Deny a request and fail the paused run."""
    return apply_approval_decision(
        service,
        runs,
        approval_id,
        decision="deny",
        operator=principal.operator_id,
        reason=payload.reason,
        ctx=ctx,
    )


@router.post("/approvals/{approval_id}/comments", response_model=ApprovalRecord)
def comment(
    approval_id: str,
    payload: ApprovalCommentRequest,
    service: ApprovalDep,
    ctx: TenantDep,
    principal: Annotated[OperatorIdentity, Depends(require_permission(Permission.APPROVE))],
) -> ApprovalRecord:
    """Append an operator comment to an approval request."""
    return service.comment(
        approval_id, author=principal.operator_id, text=payload.text, ctx=ctx
    )


@router.post("/approvals/{approval_id}/delegate", response_model=ApprovalRecord)
def delegate(
    approval_id: str,
    payload: DelegationRequest,
    service: ApprovalDep,
    ctx: TenantDep,
    principal: Annotated[OperatorIdentity, Depends(require_permission(Permission.APPROVE))],
) -> ApprovalRecord:
    """Delegate an approval request to another operator."""
    return service.delegate(
        approval_id, assignee=payload.assignee, operator=principal.operator_id, ctx=ctx
    )


def apply_approval_decision(
    approvals: ApprovalService,
    runs: RunService,
    approval_id: str,
    *,
    decision: str,
    operator: str,
    reason: str | None,
    ctx: TenantContext,
    expected_run_id: str | None = None,
) -> ApprovalRecord:
    """Apply an approval decision to its paused run, attributed to ``operator``.

    Shared by the direct approval routes and the interactive/mobile token resolver
    so both paths gate, decide, and transition the run identically. When
    ``expected_run_id`` is given (a token binding) it must match the approval's run.
    """
    if decision not in ("approve", "deny"):
        raise InvalidApprovalTokenError(f"invalid decision {decision!r}")
    approval = approvals.get(approval_id, ctx=ctx)
    if expected_run_id is not None and approval.run_id != expected_run_id:
        raise InvalidApprovalTokenError("approval token run does not match the approval")
    _require_paused(runs, approval.run_id, ctx)
    if decision == "approve":
        record = approvals.decide(
            approval_id,
            status=ApprovalStatus.APPROVED,
            operator=operator,
            reason=reason,
            ctx=ctx,
        )
        runs.intervene(
            record.run_id, InterventionAction.RESUME, actor=operator, ctx=ctx
        )
        return record
    record = approvals.decide(
        approval_id,
        status=ApprovalStatus.DENIED,
        operator=operator,
        reason=reason,
        ctx=ctx,
    )
    runs.fail(
        record.run_id,
        actor=operator,
        reason=reason or "approval denied",
        ctx=ctx,
    )
    return record


def _require_paused(
    runs: RunService, run_id: str, ctx: TenantContext
) -> None:
    """Reject a decision when the run is no longer paused, so records stay consistent."""
    if runs.get(run_id, ctx=ctx).state is not RunState.PAUSED:
        raise HTTPException(
            status_code=status.HTTP_409_CONFLICT,
            detail=f"run {run_id!r} is not paused; approval cannot be applied",
        )
