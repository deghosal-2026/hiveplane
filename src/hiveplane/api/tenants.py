"""Tenant administration API: create, suspend, reinstate, quota (M58-06)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field

from hiveplane.api.deps import (
    get_tenant_admin_service,
    get_tenant_context,
    require_permission,
)
from hiveplane.auth.models import OperatorIdentity, Permission
from hiveplane.tenancy import Tenant, TenantContext, TenantQuota
from hiveplane.tenancy.admin import TenantAdminService

router = APIRouter(tags=["tenants"])

AdminDep = Annotated[TenantAdminService, Depends(get_tenant_admin_service)]
TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]
KeyManager = Annotated[OperatorIdentity, Depends(require_permission(Permission.KEYS_MANAGE))]
FleetReader = Annotated[OperatorIdentity, Depends(require_permission(Permission.FLEET_READ))]


class TenantCreateRequest(BaseModel):
    """Request body to create a tenant."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=253)
    quota: TenantQuota | None = None


@router.post("/tenants", response_model=Tenant, status_code=status.HTTP_201_CREATED)
def create_tenant(
    payload: TenantCreateRequest,
    admin: AdminDep,
    ctx: TenantDep,
    identity: KeyManager,
) -> Tenant:
    """Create a tenant; a duplicate id is a conflict."""
    return admin.create_tenant(
        ctx, tenant_id=payload.tenant_id, name=payload.name, quota=payload.quota
    )


@router.get("/tenants", response_model=list[Tenant])
def list_tenants(
    admin: AdminDep, ctx: TenantDep, identity: FleetReader
) -> list[Tenant]:
    """List the tenants visible to the acting context."""
    return admin.list_tenants(ctx)


@router.post("/tenants/{tenant_id}/suspend", response_model=Tenant)
def suspend_tenant(
    tenant_id: str,
    admin: AdminDep,
    ctx: TenantDep,
    identity: KeyManager,
) -> Tenant:
    """Suspend a tenant, blocking runs and deliveries."""
    return admin.suspend_tenant(ctx, tenant_id, actor=identity.operator_id)


@router.post("/tenants/{tenant_id}/reinstate", response_model=Tenant)
def reinstate_tenant(
    tenant_id: str,
    admin: AdminDep,
    ctx: TenantDep,
    identity: KeyManager,
) -> Tenant:
    """Return a suspended tenant to active service."""
    return admin.reinstate_tenant(ctx, tenant_id, actor=identity.operator_id)


@router.put("/tenants/{tenant_id}/quota", response_model=Tenant)
def set_tenant_quota(
    tenant_id: str,
    quota: TenantQuota,
    admin: AdminDep,
    ctx: TenantDep,
    identity: KeyManager,
) -> Tenant:
    """Assign a tenant's resource ceilings."""
    return admin.set_quota(ctx, tenant_id, quota)
