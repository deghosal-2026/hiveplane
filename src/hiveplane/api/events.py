"""Fleet-events webhook subscription API (M56-05)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, Response, status
from pydantic import BaseModel, ConfigDict, Field

from hiveplane.api.deps import (
    get_fleet_event_service,
    get_tenant_context,
    require_permission,
)
from hiveplane.auth.models import OperatorIdentity, Permission
from hiveplane.events.models import EventSubscription, FleetEventKind
from hiveplane.events.service import FleetEventService
from hiveplane.tenancy.context import TenantContext

router = APIRouter(tags=["events"])

EventDep = Annotated[FleetEventService, Depends(get_fleet_event_service)]
FleetReader = Annotated[OperatorIdentity, Depends(require_permission(Permission.FLEET_READ))]
EventAdmin = Annotated[
    OperatorIdentity, Depends(require_permission(Permission.EVENTS_MANAGE))
]
TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]


class SubscribeRequest(BaseModel):
    """Request body to subscribe a webhook to fleet events."""

    model_config = ConfigDict(extra="forbid")

    url: str = Field(min_length=1, max_length=2048)
    kinds: list[FleetEventKind] = Field(min_length=1)


@router.post("/event-subscriptions", response_model=EventSubscription)
def subscribe(
    request: SubscribeRequest,
    service: EventDep,
    identity: EventAdmin,
    tenants: TenantDep,
) -> EventSubscription:
    """Subscribe a webhook URL to run/approval/drift/trigger events."""
    return service.subscribe(
        tenant_id=tenants.tenant_id,
        url=request.url,
        kinds=request.kinds,
        actor=identity.operator_id,
        ctx=tenants,
    )


@router.get("/event-subscriptions", response_model=list[EventSubscription])
def list_subscriptions(
    service: EventDep, identity: FleetReader, tenants: TenantDep
) -> list[EventSubscription]:
    """List the tenant's event subscriptions."""
    return service.subscriptions(tenants.tenant_id, ctx=tenants)


@router.delete("/event-subscriptions/{subscription_id}", status_code=204)
def unsubscribe(
    subscription_id: str,
    service: EventDep,
    identity: EventAdmin,
    tenants: TenantDep,
) -> Response:
    """Remove an event subscription."""
    if service.get(subscription_id, tenant_id=tenants.tenant_id, ctx=tenants) is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, "subscription not found")
    service.unsubscribe(
        subscription_id,
        tenant_id=tenants.tenant_id,
        actor=identity.operator_id,
        ctx=tenants,
    )
    return Response(status_code=204)
