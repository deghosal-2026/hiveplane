"""Secret store, API keys, auth, and access audit API (M45-08)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from hiveplane.api.deps import (
    get_auth_service as _get_auth_service,
)
from hiveplane.api.deps import (
    get_principal as _get_principal,
)
from hiveplane.api.deps import (
    get_secret_service as _get_secret_service,
)
from hiveplane.api.deps import (
    require_permission,
)
from hiveplane.auth.models import (
    AccessAuditReport,
    ApiKeyIssued,
    ApiKeyRecord,
    OperatorIdentity,
    Permission,
    Scope,
)
from hiveplane.auth.service import AuthService
from hiveplane.secrets.models import (
    SecretMetadata,
    SecretNotFoundError,
)
from hiveplane.secrets.service import SecretAlreadyExistsError, SecretService
from hiveplane.tenancy.models import Role

router = APIRouter(tags=["identity"])

SecretDep = Annotated[SecretService, Depends(_get_secret_service)]
AuthDep = Annotated[AuthService, Depends(_get_auth_service)]
PrincipalDep = Annotated[OperatorIdentity, Depends(_get_principal)]

SecretManager = Annotated[
    OperatorIdentity, Depends(require_permission(Permission.SECRETS_MANAGE))
]
KeyManager = Annotated[OperatorIdentity, Depends(require_permission(Permission.KEYS_MANAGE))]
FleetReader = Annotated[OperatorIdentity, Depends(require_permission(Permission.FLEET_READ))]


class SecretWriteRequest(BaseModel):
    """Request body to create or rotate a secret (value is write-only)."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=253)
    value: str = Field(min_length=1, repr=False)


class KeyCreateRequest(BaseModel):
    """Request body to issue a scoped API key."""

    model_config = ConfigDict(extra="forbid")

    role: Role
    scopes: list[Scope] = Field(default_factory=list)
    label: str | None = None


class LoginRequest(BaseModel):
    """Request body for an operator session login (demo/dev auth)."""

    model_config = ConfigDict(extra="forbid")

    operator_id: str = Field(min_length=1)
    tenant_id: str = Field(min_length=1, max_length=64)
    role: Role
    ip: str | None = None


@router.post("/secrets", response_model=SecretMetadata, status_code=status.HTTP_201_CREATED)
def put_secret(
    request: SecretWriteRequest, secrets: SecretDep, principal: SecretManager
) -> SecretMetadata:
    """Create a secret; the value is never returned."""
    try:
        secrets.put(principal.tenant_id, request.name, request.value)
    except SecretAlreadyExistsError as exc:
        raise HTTPException(status.HTTP_409_CONFLICT, str(exc)) from exc
    return secrets.metadata(principal.tenant_id, request.name)


@router.post("/secrets/{name}/rotate", response_model=SecretMetadata)
def rotate_secret(
    name: str, request: SecretWriteRequest, secrets: SecretDep, principal: SecretManager
) -> SecretMetadata:
    """Rotate a secret to a new current version."""
    try:
        secrets.rotate(principal.tenant_id, name, request.value)
    except SecretNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc
    return secrets.metadata(principal.tenant_id, name)


@router.get("/secrets", response_model=list[SecretMetadata])
def list_secrets(secrets: SecretDep, principal: SecretManager) -> list[SecretMetadata]:
    """List secret metadata (never plaintext)."""
    return secrets.list_secrets(principal.tenant_id)


@router.get("/secrets/{name}", response_model=SecretMetadata)
def show_secret(name: str, secrets: SecretDep, principal: SecretManager) -> SecretMetadata:
    """Show one secret's metadata."""
    try:
        return secrets.metadata(principal.tenant_id, name)
    except SecretNotFoundError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post("/keys", response_model=ApiKeyIssued, status_code=status.HTTP_201_CREATED)
def create_key(
    request: KeyCreateRequest, auth: AuthDep, principal: KeyManager
) -> ApiKeyIssued:
    """Issue a scoped API key; the token is shown exactly once."""
    return auth.keys.create(
        principal.tenant_id,
        request.role,
        scopes=request.scopes,
        label=request.label,
    )


@router.get("/keys", response_model=list[ApiKeyRecord])
def list_keys(auth: AuthDep, principal: KeyManager) -> list[ApiKeyRecord]:
    """List the tenant's keys (hashed only)."""
    return auth.keys.list_keys(principal.tenant_id)


@router.delete("/keys/{key_id}", response_model=ApiKeyRecord)
def revoke_key(key_id: str, auth: AuthDep, principal: KeyManager) -> ApiKeyRecord:
    """Revoke an API key."""
    from hiveplane.auth.models import AuthenticationError

    try:
        return auth.keys.revoke(key_id)
    except AuthenticationError as exc:
        raise HTTPException(status.HTTP_404_NOT_FOUND, str(exc)) from exc


@router.post("/auth/login", response_model=OperatorIdentity)
def login(request: LoginRequest, auth: AuthDep) -> OperatorIdentity:
    """Authenticate an operator session (dev/demo auth)."""
    return auth.login(
        request.operator_id, request.tenant_id, request.role, ip=request.ip
    )


@router.get("/auth/whoami", response_model=OperatorIdentity)
def whoami(auth: AuthDep, principal: PrincipalDep) -> OperatorIdentity:
    """Return the verified caller identity."""
    return auth.whoami(principal)


@router.get("/audit/access", response_model=AccessAuditReport)
def access_audit(auth: AuthDep, principal: FleetReader) -> AccessAuditReport:
    """Return login history and the privileged-action audit trail."""
    return AccessAuditReport(
        logins=auth.login_history(principal.tenant_id),
        events=auth.access_history(principal.tenant_id),
    )
