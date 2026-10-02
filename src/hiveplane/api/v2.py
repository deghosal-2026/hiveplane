"""API v2: versioned, paginated surface with a consistent error model (M56-01)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Query

from hiveplane import __version__
from hiveplane.api.deps import get_run_service, get_tenant_context
from hiveplane.api.pagination import Page, paginate
from hiveplane.core.run import Run, RunState
from hiveplane.execution.service import RunService
from hiveplane.tenancy import TenantContext

router = APIRouter(prefix="/v2", tags=["v2"])

ServiceDep = Annotated[RunService, Depends(get_run_service)]
TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]


@router.get("/version")
def api_version() -> dict[str, str]:
    """Return the API and control-plane version."""
    return {"api_version": "v2", "version": __version__}


@router.get("/runs", response_model=Page[Run])
def list_runs_v2(
    service: ServiceDep,
    ctx: TenantDep,
    workload: str | None = None,
    state: RunState | None = None,
    limit: int = Query(default=50, ge=1, le=200),
    cursor: str | None = None,
) -> Page[Run]:
    """List runs in a paginated envelope."""
    runs = service.list_runs(workload=workload, state=state, ctx=ctx)
    return paginate(runs, limit=limit, cursor=cursor)
