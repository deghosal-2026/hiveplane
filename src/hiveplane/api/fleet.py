"""Incident-mode API: fleet pause, resume, and state (M53, D36)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict

from hiveplane.api.deps import (
    get_incident_service,
    get_tenant_context,
    require_permission,
)
from hiveplane.auth.models import OperatorIdentity, Permission
from hiveplane.incident.models import HaltScope, IncidentRecord
from hiveplane.incident.service import IncidentService, NoActiveIncidentError
from hiveplane.tenancy import TenantContext

router = APIRouter(tags=["fleet"])

IncidentDep = Annotated[IncidentService, Depends(get_incident_service)]
TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]
FleetReader = Annotated[OperatorIdentity, Depends(require_permission(Permission.FLEET_READ))]
FleetAdmin = Annotated[OperatorIdentity, Depends(require_permission(Permission.KILL_SWITCH))]


class PauseRequest(BaseModel):
    """Request body for entering incident mode."""

    model_config = ConfigDict(extra="forbid")

    actor: str | None = None
    reason: str | None = None
    scope: HaltScope = HaltScope.FLEET
    scope_ref: str | None = None
    trigger: str = "operator"


class ResumeRequest(BaseModel):
    """Request body for leaving incident mode."""

    model_config = ConfigDict(extra="forbid")

    actor: str | None = None
    incident_id: str | None = None


class FleetState(BaseModel):
    """The current incident-mode state of the fleet."""

    model_config = ConfigDict(extra="forbid")

    halted: bool
    active: IncidentRecord | None
    history: list[IncidentRecord]


def _actor(identity: OperatorIdentity, requested: str | None) -> str:
    """Attribute the action to the authenticated operator when there is one.

    A signed-in identity always wins so an operator cannot forge a pause/resume
    as someone else; when auth is disabled the anonymous admin may pass a label.
    """
    if identity.operator_id and identity.operator_id != "anonymous":
        return identity.operator_id
    return requested or identity.operator_id


@router.post("/fleet/pause", response_model=IncidentRecord)
def pause_fleet(
    request: PauseRequest, service: IncidentDep, identity: FleetAdmin
) -> IncidentRecord:
    """Halt the fleet (or a scope): record, drain triggers, and broadcast."""
    return service.pause(
        actor=_actor(identity, request.actor),
        reason=request.reason,
        scope=request.scope,
        scope_ref=request.scope_ref,
        trigger=request.trigger,
    )


@router.post("/fleet/resume", response_model=IncidentRecord)
def resume_fleet(
    request: ResumeRequest, service: IncidentDep, identity: FleetAdmin
) -> IncidentRecord:
    """Lift the halt with attribution and an incident record."""
    try:
        return service.resume(
            actor=_actor(identity, request.actor), incident_id=request.incident_id
        )
    except NoActiveIncidentError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc


@router.get("/fleet/state", response_model=FleetState)
def fleet_state(
    service: IncidentDep, _: FleetReader, tenants: TenantDep
) -> FleetState:
    """Return the current halt state and tenant-visible incident history."""
    active = service.active_for(tenants.tenant_id)
    return FleetState(
        halted=active is not None,
        active=active,
        history=service.history_for(tenants.tenant_id),
    )
