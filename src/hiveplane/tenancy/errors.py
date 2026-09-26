"""Tenancy errors."""

from __future__ import annotations


class TenancyError(Exception):
    """Base class for tenancy failures."""


class TenantScopeError(TenancyError):
    """Raised when an operation crosses a tenant boundary."""

    def __init__(self, tenant_id: str, detail: str) -> None:
        self.tenant_id = tenant_id
        self.detail = detail
        super().__init__(f"tenant scope violation for {tenant_id!r}: {detail}")


class TenantNotFoundError(TenancyError):
    """Raised when a tenant does not exist."""

    def __init__(self, tenant_id: str) -> None:
        self.tenant_id = tenant_id
        super().__init__(f"tenant not found: {tenant_id!r}")


class TeamNotFoundError(TenancyError):
    """Raised when a team does not exist in a tenant."""

    def __init__(self, tenant_id: str, team_id: str) -> None:
        self.tenant_id = tenant_id
        self.team_id = team_id
        super().__init__(f"team not found: {team_id!r} in tenant {tenant_id!r}")
