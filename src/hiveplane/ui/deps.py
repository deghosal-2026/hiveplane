"""UI identity resolution, per-request client, and RBAC dependency (M52-01)."""

from __future__ import annotations

from collections.abc import Callable
from typing import Annotated

from fastapi import Depends, HTTPException, Request, status

from hiveplane.auth.models import Permission
from hiveplane.config import get_settings
from hiveplane.tenancy.models import Role
from hiveplane.ui.client import ControlPlaneClient
from hiveplane.ui.rbac import UiIdentity, can
from hiveplane.ui.session import SESSION_COOKIE, verify_session


def resolve_identity(request: Request) -> UiIdentity:
    """Resolve the operator identity from the signed session cookie.

    A valid cookie wins even when auth is disabled, so tests and operators can
    narrow their own identity. Without one, a disabled control plane grants an
    anonymous admin, matching :func:`hiveplane.api.deps.get_principal`.
    """
    settings = get_settings()
    token = request.cookies.get(SESSION_COOKIE)
    if token:
        session = verify_session(token, settings.ui.session_secret)
        if session is not None:
            request.state.api_key = session.api_key
            request.state.tenant_id = session.tenant_id
            identity = UiIdentity(role=session.role, operator_id=session.operator_id)
            request.state.identity = identity
            return identity
    if not settings.auth.enabled:
        identity = UiIdentity(role=Role.ADMIN)
        request.state.identity = identity
        return identity
    raise HTTPException(status.HTTP_401_UNAUTHORIZED, "login required")


def client_for(request: Request) -> ControlPlaneClient:
    """Return a control-plane client carrying the session's API key and tenant."""
    base: ControlPlaneClient = request.app.state.control_plane
    return base.with_token(
        getattr(request.state, "api_key", None),
        tenant_id=getattr(request.state, "tenant_id", None),
    )


IdentityDep = Annotated[UiIdentity, Depends(resolve_identity)]


def require_ui_permission(permission: Permission) -> Callable[[UiIdentity], UiIdentity]:
    """Build a dependency that refuses the request without ``permission``."""

    def _dependency(identity: IdentityDep) -> UiIdentity:
        if not can(identity, permission):
            raise HTTPException(status.HTTP_403_FORBIDDEN, "insufficient role")
        return identity

    return _dependency
