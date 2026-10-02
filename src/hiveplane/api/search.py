"""Cross-entity global search for the operator UI (M52-07)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict

from hiveplane.api.deps import (
    get_approval_service,
    get_registry_service,
    get_run_service,
    get_tenant_context,
    require_permission,
)
from hiveplane.auth.models import OperatorIdentity, Permission
from hiveplane.execution.service import RunService
from hiveplane.policy.approvals import ApprovalService
from hiveplane.registry.service import RegistryService
from hiveplane.tenancy import TenantContext

router = APIRouter(tags=["search"])

RunDep = Annotated[RunService, Depends(get_run_service)]
ApprovalDep = Annotated[ApprovalService, Depends(get_approval_service)]
RegistryDep = Annotated[RegistryService, Depends(get_registry_service)]
FleetReader = Annotated[OperatorIdentity, Depends(require_permission(Permission.FLEET_READ))]
TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]


class SearchHit(BaseModel):
    """One ranked search result across runs, approvals, and workloads."""

    model_config = ConfigDict(extra="forbid")

    kind: str
    identifier: str
    label: str = ""


def _matches(query: str, *fields: str) -> bool:
    """Return True when the lowercased query appears in any field."""
    needle = query.lower()
    return any(needle in field.lower() for field in fields)


@router.get("/search", response_model=list[SearchHit])
def search(
    runs: RunDep,
    approvals: ApprovalDep,
    registry: RegistryDep,
    _: FleetReader,
    ctx: TenantDep,
    q: str = "",
    limit: int = 20,
) -> list[SearchHit]:
    """Search the acting tenant's runs, approvals, and workloads, by text."""
    if not q.strip():
        return []
    hits: list[SearchHit] = []
    for run in runs.list_runs(ctx=ctx):
        if _matches(q, run.id, run.workload_id, run.caller):
            hits.append(SearchHit(kind="run", identifier=run.id, label=run.workload_id))
    for approval in approvals.list(ctx=ctx):
        if _matches(q, approval.approval_id, approval.workload, approval.rule, approval.reason):
            hits.append(
                SearchHit(kind="approval", identifier=approval.approval_id, label=approval.workload)
            )
    for record in registry.list_workloads(ctx=ctx):
        if _matches(q, record.name, record.owner, record.team or ""):
            hits.append(
                SearchHit(kind="workload", identifier=record.name, label=record.owner)
            )
    return hits[: max(limit, 0)]
