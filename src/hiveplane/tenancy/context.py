"""Tenant context carried explicitly through stores and services (D21)."""

from __future__ import annotations

from dataclasses import dataclass

from hiveplane.tenancy.errors import TenantScopeError
from hiveplane.tenancy.models import Role

DEFAULT_TENANT_ID = "default"
DEFAULT_TEAM_ID = "default"
DEFAULT_ATTRIBUTION_KEY = "default"
SYSTEM_TENANT_ID = "system"


@dataclass(frozen=True, slots=True)
class TenantContext:
    """The acting tenant identity for a store or service call."""

    tenant_id: str
    team_id: str | None = None
    operator_id: str | None = None
    role: Role = Role.VIEWER
    attribution_key: str | None = None
    is_system: bool = False

    def scopes(self, tenant_id: str) -> bool:
        """Return True when this context may see ``tenant_id``'s rows."""
        return self.is_system or self.tenant_id == tenant_id

    def require(self, tenant_id: str) -> None:
        """Raise :class:`TenantScopeError` when ``tenant_id`` is out of scope."""
        if not self.scopes(tenant_id):
            raise TenantScopeError(self.tenant_id, f"cannot access tenant {tenant_id!r}")


SYSTEM_CONTEXT = TenantContext(
    tenant_id=SYSTEM_TENANT_ID,
    role=Role.ADMIN,
    is_system=True,
)

DEFAULT_CONTEXT = TenantContext(
    tenant_id=DEFAULT_TENANT_ID,
    team_id=DEFAULT_TEAM_ID,
    attribution_key=DEFAULT_ATTRIBUTION_KEY,
    role=Role.ADMIN,
)
