"""Security-event API: query the defense telemetry stream (M39-06)."""

from __future__ import annotations

from datetime import datetime
from typing import Annotated

from fastapi import APIRouter, Depends

from hiveplane.api.deps import get_security_event_store, get_tenant_context
from hiveplane.defense.events import SecurityEvent, SecurityEventKind, SecurityEventStore
from hiveplane.tenancy import TenantContext

router = APIRouter(tags=["security"])

StoreDep = Annotated[SecurityEventStore, Depends(get_security_event_store)]
TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]


@router.get("/security/events", response_model=list[SecurityEvent])
def list_security_events(
    store: StoreDep,
    ctx: TenantDep,
    workload_id: str | None = None,
    run_id: str | None = None,
    kind: SecurityEventKind | None = None,
    since: datetime | None = None,
) -> list[SecurityEvent]:
    """List security events for the acting tenant, oldest first."""
    return store.list_events(
        workload=workload_id, run_id=run_id, kind=kind, since=since, ctx=ctx
    )
