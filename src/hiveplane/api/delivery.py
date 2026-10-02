"""Fan-out delivery audit and interactive/mobile approval API (M51)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from hiveplane.api.deps import (
    get_delivery_service,
    get_interactive_approvals,
    get_tenant_context,
    require_permission,
)
from hiveplane.auth.models import OperatorIdentity, Permission
from hiveplane.delivery.approvals import InteractiveApprovalService
from hiveplane.delivery.models import (
    AlreadyResolvedError,
    ApprovalDecisionRecord,
    DeliveryAttempt,
    InvalidApprovalTokenError,
)
from hiveplane.delivery.service import DeliveryService
from hiveplane.tenancy.context import TenantContext

router = APIRouter(tags=["delivery"])

DeliveryDep = Annotated[DeliveryService, Depends(get_delivery_service)]
ApprovalsDep = Annotated[InteractiveApprovalService, Depends(get_interactive_approvals)]
FleetReader = Annotated[OperatorIdentity, Depends(require_permission(Permission.FLEET_READ))]
Approver = Annotated[OperatorIdentity, Depends(require_permission(Permission.APPROVE))]
TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]


class ResolveRequest(BaseModel):
    """Request body to resolve an interactive/mobile approval token."""

    model_config = ConfigDict(extra="forbid")

    token: str = Field(repr=False)
    decision: str = Field(pattern="^(approve|deny)$")
    reason: str | None = None
    channel: str = "api"


@router.post("/delivery/approvals/resolve", response_model=ApprovalDecisionRecord)
def resolve_approval(
    request: ResolveRequest,
    approvals: ApprovalsDep,
    principal: Approver,
    tenants: TenantDep,
) -> ApprovalDecisionRecord:
    """Resolve a signed approval token exactly once, attributed to the caller."""
    try:
        return approvals.resolve(
            request.token,
            decision=request.decision,
            operator_id=principal.operator_id,
            reason=request.reason,
            channel=request.channel,
            tenant_id=tenants.tenant_id,
            ctx=tenants,
        )
    except AlreadyResolvedError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    except InvalidApprovalTokenError as exc:
        raise HTTPException(status.HTTP_401_UNAUTHORIZED, str(exc)) from exc


@router.get("/delivery/audit", response_model=list[DeliveryAttempt])
def delivery_audit(
    delivery: DeliveryDep,
    _: FleetReader,
    tenants: TenantDep,
) -> list[DeliveryAttempt]:
    """Return the acting tenant's fan-out delivery audit (destination, attempts, status)."""
    return delivery.attempts(tenants.tenant_id, ctx=tenants)
