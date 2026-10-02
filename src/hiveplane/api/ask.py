"""The `ask` copilot API: read-only, attributed NL Q&A (M53, D36)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from hiveplane.api.deps import (
    get_ask_service,
    get_registry_service,
    get_run_service,
    get_tenant_context,
    require_permission,
)
from hiveplane.ask.models import AskAnswer
from hiveplane.ask.service import AskService
from hiveplane.ask.workload import ASK_WORKLOAD_NAME
from hiveplane.auth.models import OperatorIdentity, Permission
from hiveplane.core.run import AdmissionContext
from hiveplane.execution.service import RunService
from hiveplane.registry.errors import WorkloadNotFoundError
from hiveplane.registry.service import RegistryService
from hiveplane.tenancy import TenantContext

router = APIRouter(tags=["ask"])

AskDep = Annotated[AskService, Depends(get_ask_service)]
RunDep = Annotated[RunService, Depends(get_run_service)]
RegistryDep = Annotated[RegistryService, Depends(get_registry_service)]
TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]
FleetReader = Annotated[OperatorIdentity, Depends(require_permission(Permission.FLEET_READ))]


class AskRequest(BaseModel):
    """A natural-language operator question."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1)


@router.post("/ask", response_model=AskAnswer)
def ask(
    request: AskRequest,
    service: AskDep,
    identity: FleetReader,
    tenants: TenantDep,
    runs: RunDep,
    registry: RegistryDep,
) -> AskAnswer:
    """Answer a live-state question; read-only and attributed to the caller."""
    _admit_ask_workload(runs, registry, tenants, request.question)
    return service.query(
        request.question,
        tenant_id=identity.tenant_id or tenants.tenant_id,
        operator_id=identity.operator_id,
    )


def _admit_ask_workload(
    runs: RunService,
    registry: RegistryService,
    ctx: TenantContext,
    question: str,
) -> None:
    """Run the registered `ask` workload through admission so its budget,
    certification, and policy gates are exercised (M53-03); a plane without a
    registered `ask` workload keeps the legacy direct-answer path.
    """
    try:
        registry.get(ASK_WORKLOAD_NAME, ctx=ctx)
    except WorkloadNotFoundError:
        return
    runs.submit(
        workload=ASK_WORKLOAD_NAME,
        caller="ask-api",
        context=AdmissionContext.PRODUCTION,
        task={"question": question},
        read_only=True,
        ctx=ctx,
    )
