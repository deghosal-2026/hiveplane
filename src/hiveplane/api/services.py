"""Agent-as-service endpoints: serve a certified workload over HTTP (M56-03).

Every invocation runs through the normal control plane: authenticated, then
admission (certification, model binding, budget, policy, sandbox), so a service
endpoint is exactly a run submission addressed to one workload.
"""

from __future__ import annotations

from typing import Annotated, Any

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field

from hiveplane.api.deps import get_run_service, require_permission
from hiveplane.auth.models import OperatorIdentity, Permission
from hiveplane.core.run import AdmissionContext, Run
from hiveplane.execution.service import RunService
from hiveplane.tenancy import TenantContext

router = APIRouter(tags=["services"])

ServiceDep = Annotated[RunService, Depends(get_run_service)]
Invoker = Annotated[OperatorIdentity, Depends(require_permission(Permission.FLEET_READ))]


class ServiceInvokeRequest(BaseModel):
    """Request body to invoke a workload as a service."""

    model_config = ConfigDict(extra="forbid")

    task: dict[str, Any] = Field(default_factory=dict)
    caller: str = Field(default="service", min_length=1, max_length=253)
    context: AdmissionContext = AdmissionContext.PRODUCTION
    model_identity: str | None = None


@router.post(
    "/services/{workload}/invoke",
    response_model=Run,
    status_code=status.HTTP_201_CREATED,
)
def invoke_workload(
    workload: str, request: ServiceInvokeRequest, service: ServiceDep, identity: Invoker
) -> Run:
    """Invoke a workload; admission, budget, and policy gate the run."""
    ctx = TenantContext(tenant_id=identity.tenant_id, role=identity.role)
    return service.submit(
        workload=workload,
        caller=request.caller,
        context=request.context,
        task=request.task,
        model_identity=request.model_identity,
        ctx=ctx,
    )
