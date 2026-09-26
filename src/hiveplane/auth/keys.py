"""Scoped API keys: issuance, hashing, authentication, revocation (M45-05)."""

from __future__ import annotations

import hashlib
import secrets as _secrets
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from hiveplane.auth.models import (
    ApiKeyIssued,
    ApiKeyRecord,
    AuthenticationError,
    AuthMethod,
    OperatorIdentity,
    Scope,
)
from hiveplane.auth.store import AuthStore
from hiveplane.tenancy.models import Role

_TOKEN_PREFIX = "hp_"


def hash_api_key(token: str) -> str:
    """Return the stored hash of a plaintext API key (never store the token)."""
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def new_key_id() -> str:
    """Return a fresh opaque API-key id."""
    return f"key-{uuid4().hex[:20]}"


class ApiKeyService:
    """Issues and authenticates scoped API keys for a tenant."""

    def __init__(
        self,
        store: AuthStore,
        *,
        clock: Callable[[], datetime] | None = None,
        key_id_factory: Callable[[], str] = new_key_id,
        token_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._key_id_factory = key_id_factory
        self._token_factory = token_factory or (
            lambda: _TOKEN_PREFIX + _secrets.token_urlsafe(32)
        )

    def create(
        self,
        tenant_id: str,
        role: Role,
        *,
        scopes: list[Scope] | None = None,
        label: str | None = None,
    ) -> ApiKeyIssued:
        """Issue a key; the plaintext token is returned exactly once."""
        token = self._token_factory()
        record = ApiKeyRecord(
            key_id=self._key_id_factory(),
            tenant_id=tenant_id,
            role=role,
            scopes=list(scopes or []),
            hashed_key=hash_api_key(token),
            label=label,
            created_at=self._clock(),
        )
        self._store.save_key(record)
        return ApiKeyIssued(key_id=record.key_id, token=token, record=record)

    def authenticate(self, token: str) -> OperatorIdentity:
        """Resolve a token to an identity, failing closed on unknown/revoked."""
        record = self._store.find_key_by_hash(hash_api_key(token))
        if record is None or record.revoked_at is not None:
            raise AuthenticationError("invalid or revoked API key")
        now = self._clock()
        record.last_used_at = now
        self._store.save_key(record)
        return OperatorIdentity(
            operator_id=record.key_id,
            tenant_id=record.tenant_id,
            role=record.role,
            method=AuthMethod.API_KEY,
            scopes=record.scopes,
            key_id=record.key_id,
        )

    def revoke(self, key_id: str) -> ApiKeyRecord:
        """Revoke a key, failing closed if it does not exist."""
        record = self._store.get_key(key_id)
        if record is None:
            raise AuthenticationError(f"unknown API key {key_id!r}")
        record.revoked_at = self._clock()
        self._store.save_key(record)
        return record

    def list_keys(self, tenant_id: str) -> list[ApiKeyRecord]:
        """Return the tenant's keys (hashed only; no plaintext)."""
        return self._store.list_keys(tenant_id)

    def usage(self, tenant_id: str) -> dict[str, datetime | None]:
        """Return key id -> last-used timestamp for usage analytics."""
        return {record.key_id: record.last_used_at for record in self._store.list_keys(tenant_id)}
