"""RBAC-lite: role permissions, scoped narrowing, server-side checks (M45-05/06)."""

from __future__ import annotations

from collections.abc import Iterable

from hiveplane.auth.models import (
    AuthorizationError,
    OperatorIdentity,
    Permission,
    Scope,
)
from hiveplane.tenancy.models import Role

ROLE_PERMISSIONS: dict[Role, frozenset[Permission]] = {
    Role.ADMIN: frozenset(
        {
            Permission.APPROVE,
            Permission.PROMOTE,
            Permission.KILL_SWITCH,
            Permission.SECRETS_MANAGE,
            Permission.KEYS_MANAGE,
            Permission.FLEET_READ,
        }
    ),
    Role.APPROVER: frozenset({Permission.APPROVE, Permission.FLEET_READ}),
    Role.VIEWER: frozenset({Permission.FLEET_READ}),
}

SCOPE_PERMISSIONS: dict[Scope, frozenset[Permission]] = {
    Scope.RUNS_READ: frozenset({Permission.FLEET_READ}),
    Scope.FLEET_READ: frozenset({Permission.FLEET_READ}),
    Scope.APPROVALS_WRITE: frozenset({Permission.APPROVE}),
    Scope.PROMOTIONS_WRITE: frozenset({Permission.PROMOTE}),
    Scope.KILL_SWITCH_WRITE: frozenset({Permission.KILL_SWITCH}),
    Scope.SECRETS_MANAGE: frozenset({Permission.SECRETS_MANAGE}),
    Scope.KEYS_MANAGE: frozenset({Permission.KEYS_MANAGE}),
}


def effective_permissions(
    role: Role, scopes: Iterable[Scope] = ()
) -> frozenset[Permission]:
    """Return the permissions a role+scope set actually grants.

    Scopes narrow, never widen: an empty scope set grants the whole role; a
    non-empty set intersects the role's permissions with the scope-implied ones,
    so a scoped key can never exceed its scope.
    """
    role_perms = ROLE_PERMISSIONS[role]
    scope_list = list(scopes)
    if not scope_list:
        return role_perms
    allowed: set[Permission] = set()
    for scope in scope_list:
        allowed |= SCOPE_PERMISSIONS[scope]
    return frozenset(role_perms & allowed)


def has_permission(identity: OperatorIdentity, permission: Permission) -> bool:
    """Return True when the identity may perform ``permission``."""
    return permission in effective_permissions(identity.role, identity.scopes)


def authorize(identity: OperatorIdentity, permission: Permission) -> None:
    """Raise :class:`AuthorizationError` unless the identity has the permission."""
    if not has_permission(identity, permission):
        raise AuthorizationError(identity.operator_id, permission.value)
