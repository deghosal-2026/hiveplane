"""Authentication, server-side authorization, and access audit (M45-06/07)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane.auth.keys import ApiKeyService
from hiveplane.auth.models import (
    AccessEvent,
    AccessResult,
    AuthMethod,
    AuthorizationError,
    LoginEvent,
    OperatorIdentity,
    Permission,
    Scope,
)
from hiveplane.auth.rbac import authorize as rbac_authorize
from hiveplane.auth.store import AuthStore
from hiveplane.tenancy.models import Role


class AuthService:
    """Authenticates operators and enforces privileged actions server-side."""

    def __init__(
        self,
        store: AuthStore,
        *,
        clock: Callable[[], datetime] | None = None,
        keys: ApiKeyService | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._keys = keys or ApiKeyService(store, clock=self._clock)

    @property
    def keys(self) -> ApiKeyService:
        """Return the API-key service sharing this store."""
        return self._keys

    def login(
        self,
        operator_id: str,
        tenant_id: str,
        role: Role,
        *,
        method: AuthMethod = AuthMethod.SESSION,
        scopes: list[Scope] | None = None,
        ip: str | None = None,
    ) -> OperatorIdentity:
        """Authenticate an operator via session or key and record the login."""
        identity = OperatorIdentity(
            operator_id=operator_id,
            tenant_id=tenant_id,
            role=role,
            method=method,
            scopes=list(scopes or []),
        )
        self._store.save_login(
            LoginEvent(
                tenant_id=tenant_id,
                actor=operator_id,
                method=method,
                ip=ip,
                result=AccessResult.ALLOW,
                created_at=self._clock(),
            )
        )
        return identity

    def authenticate_key(self, token: str, *, ip: str | None = None) -> OperatorIdentity:
        """Authenticate a scoped API key and record the login."""
        identity = self._keys.authenticate(token)
        self._store.save_login(
            LoginEvent(
                tenant_id=identity.tenant_id,
                actor=identity.operator_id,
                method=AuthMethod.API_KEY,
                ip=ip,
                result=AccessResult.ALLOW,
                created_at=self._clock(),
            )
        )
        return identity

    def whoami(self, identity: OperatorIdentity) -> OperatorIdentity:
        """Return the verified identity (used by ``auth whoami``)."""
        return identity

    def authorize(
        self,
        identity: OperatorIdentity,
        permission: Permission,
        *,
        action: str | None = None,
        target: str | None = None,
    ) -> None:
        """Authorize server-side, auditing both the allow and the deny."""
        label = action or permission.value
        try:
            rbac_authorize(identity, permission)
        except AuthorizationError:
            self._store.save_access(
                AccessEvent(
                    tenant_id=identity.tenant_id,
                    actor=identity.operator_id,
                    method=identity.method,
                    action=label,
                    target=target,
                    result=AccessResult.DENY,
                    created_at=self._clock(),
                )
            )
            raise
        self._store.save_access(
            AccessEvent(
                tenant_id=identity.tenant_id,
                actor=identity.operator_id,
                method=identity.method,
                action=label,
                target=target,
                result=AccessResult.ALLOW,
                created_at=self._clock(),
            )
        )

    def login_history(self, tenant_id: str) -> list[LoginEvent]:
        """Return the tenant's login history."""
        return self._store.list_logins(tenant_id)

    def access_history(self, tenant_id: str) -> list[AccessEvent]:
        """Return the tenant's privileged-action audit trail."""
        return self._store.list_access(tenant_id)


def build_auth_service(
    store: AuthStore | None = None, *, settings: object | None = None
) -> AuthService:
    """Build an :class:`AuthService` over the configured store."""
    from hiveplane.auth.store import build_auth_store

    if store is None:
        store = build_auth_store(settings)  # type: ignore[arg-type]
    return AuthService(store)
