"""Tenancy: isolation boundary, teams, memberships, and scoping context."""

from __future__ import annotations

from hiveplane.tenancy.context import (
    DEFAULT_ATTRIBUTION_KEY,
    DEFAULT_CONTEXT,
    DEFAULT_TEAM_ID,
    DEFAULT_TENANT_ID,
    SYSTEM_CONTEXT,
    SYSTEM_TENANT_ID,
    TenantContext,
)
from hiveplane.tenancy.errors import (
    TeamNotFoundError,
    TenancyError,
    TenantNotFoundError,
    TenantScopeError,
)
from hiveplane.tenancy.models import Membership, Role, Team, Tenant

__all__ = [
    "DEFAULT_ATTRIBUTION_KEY",
    "DEFAULT_CONTEXT",
    "DEFAULT_TEAM_ID",
    "DEFAULT_TENANT_ID",
    "SYSTEM_CONTEXT",
    "SYSTEM_TENANT_ID",
    "Membership",
    "Role",
    "Team",
    "TeamNotFoundError",
    "TenancyError",
    "Tenant",
    "TenantContext",
    "TenantNotFoundError",
    "TenantScopeError",
]
