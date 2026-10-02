"""Federation API: register remote planes and aggregate the fleet (M59-08)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field

from hiveplane.api.deps import get_federation_service, require_permission
from hiveplane.auth.models import OperatorIdentity, Permission
from hiveplane.federation.models import AggregateView, RemotePlane
from hiveplane.federation.service import FederationService

router = APIRouter(tags=["federation"])

FederationDep = Annotated[FederationService, Depends(get_federation_service)]
FleetReader = Annotated[OperatorIdentity, Depends(require_permission(Permission.FLEET_READ))]
FederationAdmin = Annotated[
    OperatorIdentity, Depends(require_permission(Permission.KEYS_MANAGE))
]


class RegisterPlaneRequest(BaseModel):
    """Request body to register a remote plane."""

    model_config = ConfigDict(extra="forbid")

    plane_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=253)
    base_url: str = Field(min_length=1, max_length=512)
    workload_count: int = Field(default=0, ge=0)


@router.get("/federation/planes", response_model=list[RemotePlane])
def list_planes(service: FederationDep, _: FleetReader) -> list[RemotePlane]:
    """List registered remote planes."""
    return service.list_planes()


@router.post(
    "/federation/planes",
    response_model=RemotePlane,
    status_code=status.HTTP_201_CREATED,
)
def register_plane(
    request: RegisterPlaneRequest, service: FederationDep, _: FederationAdmin
) -> RemotePlane:
    """Register (or refresh) a remote plane."""
    return service.register(
        plane_id=request.plane_id,
        name=request.name,
        base_url=request.base_url,
        workload_count=request.workload_count,
    )


@router.get("/federation/aggregate", response_model=AggregateView)
def aggregate(service: FederationDep, _: FleetReader) -> AggregateView:
    """Return the aggregate fleet view across registered planes."""
    return service.aggregate()
