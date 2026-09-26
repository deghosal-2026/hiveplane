"""Operator identity, API keys, and access-audit models (M45-05..M45-07)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from hiveplane.tenancy.models import Role


class AuthError(Exception):
    """Base class for authentication/authorization failures."""


class AuthenticationError(AuthError):
    """Raised when a caller cannot be authenticated (HTTP 401)."""


class AuthorizationError(AuthError):
    """Raised when an authenticated caller lacks a permission (HTTP 403)."""

    def __init__(self, actor: str, permission: str) -> None:
        super().__init__(f"actor {actor!r} lacks permission {permission!r}")
        self.actor = actor
        self.permission = permission


class Permission(StrEnum):
    """A privileged action checked server-side."""

    APPROVE = "approve"
    PROMOTE = "promote"
    KILL_SWITCH = "kill_switch"
    SECRETS_MANAGE = "secrets_manage"
    KEYS_MANAGE = "keys_manage"
    FLEET_READ = "fleet_read"


class Scope(StrEnum):
    """An optional narrowing of an API key's authority."""

    RUNS_READ = "runs:read"
    FLEET_READ = "fleet:read"
    APPROVALS_WRITE = "approvals:write"
    PROMOTIONS_WRITE = "promotions:write"
    KILL_SWITCH_WRITE = "kill-switch:write"
    SECRETS_MANAGE = "secrets:manage"
    KEYS_MANAGE = "keys:manage"


class AuthMethod(StrEnum):
    """How a caller authenticated."""

    SESSION = "session"
    API_KEY = "api_key"


class OperatorIdentity(BaseModel):
    """An authenticated operator for a tenant."""

    model_config = ConfigDict(extra="forbid")

    operator_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1, max_length=64)
    role: Role
    method: AuthMethod
    scopes: list[Scope] = Field(default_factory=list)
    key_id: str | None = None


class ApiKeyRecord(BaseModel):
    """A stored (hashed) scoped API key."""

    model_config = ConfigDict(extra="forbid")

    key_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1, max_length=64)
    role: Role
    scopes: list[Scope] = Field(default_factory=list)
    hashed_key: str = Field(min_length=1)
    label: str | None = None
    created_at: AwareDatetime
    last_used_at: AwareDatetime | None = None
    revoked_at: AwareDatetime | None = None


class ApiKeyIssued(BaseModel):
    """A newly issued key; the plaintext token is shown exactly once."""

    model_config = ConfigDict(extra="forbid")

    key_id: str = Field(min_length=1)
    token: str = Field(repr=False)
    record: ApiKeyRecord


class AccessResult(StrEnum):
    """The outcome of an audited access."""

    ALLOW = "allow"
    DENY = "deny"


class LoginEvent(BaseModel):
    """An operator login/authentication record."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1, max_length=64)
    actor: str = Field(min_length=1)
    method: AuthMethod
    ip: str | None = None
    result: AccessResult
    created_at: AwareDatetime


class AccessEvent(BaseModel):
    """An audited privileged action (allow or deny), append-only."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(min_length=1, max_length=64)
    actor: str = Field(min_length=1)
    method: AuthMethod
    action: str = Field(min_length=1)
    target: str | None = None
    result: AccessResult
    created_at: AwareDatetime


class AccessAuditReport(BaseModel):
    """A tenant's access audit: login history and privileged-action trail."""

    model_config = ConfigDict(extra="forbid")

    logins: list[LoginEvent] = Field(default_factory=list)
    events: list[AccessEvent] = Field(default_factory=list)
