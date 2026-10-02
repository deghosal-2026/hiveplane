"""Authentication, server-side authorization, and access audit (M45-06/07)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime, timedelta

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
from hiveplane.tenancy.context import (
    DEFAULT_CONTEXT,
    SYSTEM_CONTEXT,
    SYSTEM_TENANT_ID,
    TenantContext,
    context_for_run,
)
from hiveplane.tenancy.errors import TenantNotFoundError
from hiveplane.tenancy.models import Role
from hiveplane.tenancy.store import TenantStore


class AuthService:
    """Authenticates operators and enforces privileged actions server-side."""

    def __init__(
        self,
        store: AuthStore,
        *,
        clock: Callable[[], datetime] | None = None,
        keys: ApiKeyService | None = None,
        tenant_store: TenantStore | None = None,
        require_active: Callable[[TenantContext, str], object] | None = None,
        auth_enabled: bool = False,
        login_interval_seconds: int = 60,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._keys = keys or ApiKeyService(store, clock=self._clock)
        self._tenant_store = tenant_store
        self._require_active = require_active
        self._auth_enabled = auth_enabled
        self._login_interval = timedelta(seconds=max(0, login_interval_seconds))
        self._recent_logins: dict[tuple[str, str, str], datetime] = {}

    @property
    def keys(self) -> ApiKeyService:
        """Return the API-key service sharing this store."""
        return self._keys

    @property
    def store(self) -> AuthStore:
        """Return the auth store backing this service."""
        return self._store

    def _ensure_active(self, tenant_id: str) -> None:
        """Refuse a suspended tenant; unknown tenants pass through."""
        if self._require_active is None:
            return
        ctx = (
            SYSTEM_CONTEXT
            if tenant_id == SYSTEM_TENANT_ID
            else context_for_run(tenant_id)
        )
        try:
            self._require_active(ctx, tenant_id)
        except TenantNotFoundError:
            return

    def _resolve_role(self, tenant_id: str, operator_id: str, supplied: Role) -> Role:
        """Resolve the operator's role from membership, else fail safe."""
        if self._tenant_store is None:
            return Role.VIEWER if self._auth_enabled else supplied
        membership = self._tenant_store.find_membership(
            context_for_run(tenant_id), tenant_id, operator_id
        )
        if membership is not None:
            return membership.role
        return Role.VIEWER if self._auth_enabled else supplied

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
        self._ensure_active(tenant_id)
        identity = OperatorIdentity(
            operator_id=operator_id,
            tenant_id=tenant_id,
            role=self._resolve_role(tenant_id, operator_id, role),
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
            ),
            ctx=context_for_run(tenant_id),
        )
        return identity

    def authenticate_key(self, token: str, *, ip: str | None = None) -> OperatorIdentity:
        """Authenticate a scoped API key and sample the login audit.

        ``last_used_at`` is always refreshed by the key service; the append-only
        login row is sampled per key/window so a high-request-rate tenant cannot
        grow the audit table without bound (#544).
        """
        identity = self._keys.authenticate(token)
        self._ensure_active(identity.tenant_id)
        if self._should_record_login(identity, AuthMethod.API_KEY):
            self._store.save_login(
                LoginEvent(
                    tenant_id=identity.tenant_id,
                    actor=identity.operator_id,
                    method=AuthMethod.API_KEY,
                    ip=ip,
                    result=AccessResult.ALLOW,
                    created_at=self._clock(),
                ),
                ctx=context_for_run(identity.tenant_id),
            )
        return identity

    def _should_record_login(
        self, identity: OperatorIdentity, method: AuthMethod
    ) -> bool:
        """Return whether a login row is due for this identity within the window."""
        key = (identity.tenant_id, identity.operator_id, method.value)
        now = self._clock()
        last = self._recent_logins.get(key)
        if last is not None and now - last < self._login_interval:
            return False
        self._recent_logins[key] = now
        return True

    def whoami(self, identity: OperatorIdentity) -> OperatorIdentity:
        """Return the verified identity (used by ``auth whoami``)."""
        return identity

    def _effective_identity(self, identity: OperatorIdentity) -> OperatorIdentity:
        """Narrow an identity to its membership role when one is recorded."""
        if self._tenant_store is None:
            return identity
        membership = self._tenant_store.find_membership(
            context_for_run(identity.tenant_id),
            identity.tenant_id,
            identity.operator_id,
        )
        if membership is None or membership.role is identity.role:
            return identity
        return identity.model_copy(update={"role": membership.role})

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
        effective = self._effective_identity(identity)
        try:
            rbac_authorize(effective, permission)
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
                ),
                ctx=context_for_run(identity.tenant_id),
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
            ),
            ctx=context_for_run(identity.tenant_id),
        )

    def login_history(
        self,
        tenant_id: str,
        *,
        since: datetime | None = None,
        limit: int | None = None,
        offset: int = 0,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[LoginEvent]:
        """Return the tenant's login history, optionally bounded."""
        return self._store.list_logins(
            tenant_id, since=since, limit=limit, offset=offset, ctx=ctx
        )

    def access_history(
        self,
        tenant_id: str,
        *,
        since: datetime | None = None,
        limit: int | None = None,
        offset: int = 0,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[AccessEvent]:
        """Return the tenant's privileged-action audit trail, optionally bounded."""
        return self._store.list_access(
            tenant_id, since=since, limit=limit, offset=offset, ctx=ctx
        )


def build_auth_service(
    store: AuthStore | None = None,
    *,
    settings: object | None = None,
    tenant_store: TenantStore | None = None,
    require_active: Callable[[TenantContext, str], object] | None = None,
    auth_enabled: bool = False,
) -> AuthService:
    """Build an :class:`AuthService` over the configured store."""
    from hiveplane.auth.store import build_auth_store

    if store is None:
        store = build_auth_store(settings)  # type: ignore[arg-type]
    return AuthService(
        store,
        tenant_store=tenant_store,
        require_active=require_active,
        auth_enabled=auth_enabled,
    )
