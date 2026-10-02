"""Tenant administration: create, suspend, reinstate, and quota (M58-06)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane.tenancy.context import SYSTEM_CONTEXT, TenantContext
from hiveplane.tenancy.errors import (
    TenantAlreadyExistsError,
    TenantNotFoundError,
    TenantScopeError,
    TenantSuspendedError,
)
from hiveplane.tenancy.models import Role, Tenant, TenantQuota, TenantStatus
from hiveplane.tenancy.store import TenantStore


class TenantAdminService:
    """Authorizes and applies tenant lifecycle operations.

    Tenant creation is system-only, or trusted-local when the plane runs with
    authentication disabled. Suspension, reinstatement, and quota may be applied
    by the system/trusted-local context for any tenant, or by a tenant's own
    ADMIN for that tenant alone; every other actor is refused.
    """

    def __init__(
        self,
        store: TenantStore,
        *,
        clock: Callable[[], datetime] | None = None,
        trusted_local: bool = False,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._trusted_local = trusted_local

    @staticmethod
    def _require_system(ctx: TenantContext, *, trusted_local: bool) -> None:
        """Reject every non-system actor; ``TenantScopeError`` maps to HTTP 403."""
        if not (ctx.is_system or trusted_local):
            raise TenantScopeError(
                ctx.tenant_id, "creating a tenant requires the system context"
            )

    def _admin_ctx(self, ctx: TenantContext, tenant_id: str) -> TenantContext:
        """Return the context a mutation runs under, or refuse the actor."""
        if ctx.is_system or self._trusted_local:
            return SYSTEM_CONTEXT
        if ctx.role is Role.ADMIN and ctx.tenant_id == tenant_id:
            return ctx
        raise TenantScopeError(
            ctx.tenant_id, f"cannot administer tenant {tenant_id!r}"
        )

    def create_tenant(
        self,
        ctx: TenantContext,
        *,
        tenant_id: str,
        name: str,
        quota: TenantQuota | None = None,
    ) -> Tenant:
        """Create a tenant; system or trusted-local only."""
        self._require_system(ctx, trusted_local=self._trusted_local)
        if self._store.get_tenant(SYSTEM_CONTEXT, tenant_id) is not None:
            raise TenantAlreadyExistsError(tenant_id)
        tenant = Tenant(
            tenant_id=tenant_id,
            name=name,
            created_at=self._clock(),
            quota=quota if quota is not None else TenantQuota(),
        )
        self._store.save_tenant(SYSTEM_CONTEXT, tenant)
        return tenant

    def suspend_tenant(
        self, ctx: TenantContext, tenant_id: str, *, actor: str = "operator"
    ) -> Tenant:
        """Suspend a tenant; suspended tenants cannot run or receive deliveries."""
        write_ctx = self._admin_ctx(ctx, tenant_id)
        return self._store.set_status(write_ctx, tenant_id, TenantStatus.SUSPENDED)

    def reinstate_tenant(
        self, ctx: TenantContext, tenant_id: str, *, actor: str = "operator"
    ) -> Tenant:
        """Return a suspended tenant to active service."""
        write_ctx = self._admin_ctx(ctx, tenant_id)
        return self._store.set_status(write_ctx, tenant_id, TenantStatus.ACTIVE)

    def set_quota(
        self, ctx: TenantContext, tenant_id: str, quota: TenantQuota
    ) -> Tenant:
        """Assign a tenant's resource ceilings."""
        write_ctx = self._admin_ctx(ctx, tenant_id)
        return self._store.set_quota(write_ctx, tenant_id, quota)

    def require_active(self, ctx: TenantContext, tenant_id: str) -> Tenant:
        """Return an active tenant or raise; the system context bypasses status."""
        tenant = self._store.get_tenant(SYSTEM_CONTEXT, tenant_id)
        if tenant is None:
            raise TenantNotFoundError(tenant_id)
        if tenant.status is not TenantStatus.ACTIVE and not ctx.is_system:
            raise TenantSuspendedError(tenant_id)
        return tenant

    def list_tenants(self, ctx: TenantContext) -> list[Tenant]:
        """List the tenants visible to the acting context."""
        return self._store.list_tenants(ctx)
