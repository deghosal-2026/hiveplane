"""Operator auth, RBAC-lite, scoped API keys, and access audit (M45)."""

from __future__ import annotations

from hiveplane.auth.keys import ApiKeyService, hash_api_key, new_key_id
from hiveplane.auth.models import (
    AccessEvent,
    AccessResult,
    ApiKeyIssued,
    ApiKeyRecord,
    AuthenticationError,
    AuthError,
    AuthMethod,
    AuthorizationError,
    LoginEvent,
    OperatorIdentity,
    Permission,
    Scope,
)
from hiveplane.auth.rbac import (
    ROLE_PERMISSIONS,
    SCOPE_PERMISSIONS,
    authorize,
    effective_permissions,
    has_permission,
)
from hiveplane.auth.service import AuthService, build_auth_service
from hiveplane.auth.store import (
    AuthStore,
    InMemoryAuthStore,
    PostgresAuthStore,
    build_auth_store,
)

__all__ = [
    "ROLE_PERMISSIONS",
    "SCOPE_PERMISSIONS",
    "AccessEvent",
    "AccessResult",
    "ApiKeyIssued",
    "ApiKeyRecord",
    "ApiKeyService",
    "AuthError",
    "AuthMethod",
    "AuthService",
    "AuthStore",
    "AuthenticationError",
    "AuthorizationError",
    "InMemoryAuthStore",
    "LoginEvent",
    "OperatorIdentity",
    "Permission",
    "PostgresAuthStore",
    "Scope",
    "authorize",
    "build_auth_service",
    "build_auth_store",
    "effective_permissions",
    "has_permission",
    "hash_api_key",
    "new_key_id",
]
