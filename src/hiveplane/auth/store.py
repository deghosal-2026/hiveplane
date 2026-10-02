"""Storage for API keys and the access audit (M45-05..M45-07)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, cast

from sqlalchemy import Engine, delete, select
from sqlalchemy.engine import CursorResult

from hiveplane.auth.models import AccessEvent, ApiKeyRecord, LoginEvent
from hiveplane.config import Settings, get_settings
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import AccessAuditRow, ApiKeyRow
from hiveplane.tenancy.context import DEFAULT_CONTEXT, TenantContext


class AuthStore(Protocol):
    """Storage interface for API keys and access-audit events."""

    def save_key(
        self, record: ApiKeyRecord, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def get_key(
        self, key_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> ApiKeyRecord | None: ...

    def find_key_by_hash(self, hashed_key: str) -> ApiKeyRecord | None: ...

    def list_keys(
        self, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[ApiKeyRecord]: ...

    def save_login(
        self, event: LoginEvent, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def list_logins(
        self,
        tenant_id: str,
        *,
        since: datetime | None = None,
        limit: int | None = None,
        offset: int = 0,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[LoginEvent]: ...

    def save_access(
        self, event: AccessEvent, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def list_access(
        self,
        tenant_id: str,
        *,
        since: datetime | None = None,
        limit: int | None = None,
        offset: int = 0,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[AccessEvent]: ...

    def delete_events_before(
        self, *, cutoff: datetime, tenant_id: str, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> int: ...

    def purge_tenant(
        self, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> int: ...

    def clear(self) -> None: ...


class InMemoryAuthStore:
    """A process-local auth store."""

    def __init__(self) -> None:
        self._keys: dict[str, ApiKeyRecord] = {}
        self._logins: list[LoginEvent] = []
        self._access: list[AccessEvent] = []

    def save_key(self, record: ApiKeyRecord, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(record.tenant_id)
        self._keys[record.key_id] = record.model_copy(deep=True)

    def get_key(self, key_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT) -> ApiKeyRecord | None:
        record = self._keys.get(key_id)
        if record is None or not ctx.scopes(record.tenant_id):
            return None
        return record.model_copy(deep=True)

    def find_key_by_hash(self, hashed_key: str) -> ApiKeyRecord | None:
        for record in self._keys.values():
            if record.hashed_key == hashed_key:
                return record.model_copy(deep=True)
        return None

    def list_keys(
        self, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[ApiKeyRecord]:
        if not ctx.scopes(tenant_id):
            return []
        records = [
            record.model_copy(deep=True)
            for _, record in sorted(self._keys.items())
            if record.tenant_id == tenant_id
        ]
        return records

    def save_login(self, event: LoginEvent, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(event.tenant_id)
        self._logins.append(event.model_copy(deep=True))

    def list_logins(
        self,
        tenant_id: str,
        *,
        since: datetime | None = None,
        limit: int | None = None,
        offset: int = 0,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[LoginEvent]:
        if not ctx.scopes(tenant_id):
            return []
        events = [
            e
            for e in self._logins
            if e.tenant_id == tenant_id and (since is None or e.created_at >= since)
        ]
        events = events[offset:]
        if limit is not None:
            events = events[:limit]
        return [e.model_copy(deep=True) for e in events]

    def save_access(self, event: AccessEvent, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(event.tenant_id)
        self._access.append(event.model_copy(deep=True))

    def list_access(
        self,
        tenant_id: str,
        *,
        since: datetime | None = None,
        limit: int | None = None,
        offset: int = 0,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[AccessEvent]:
        if not ctx.scopes(tenant_id):
            return []
        events = [
            e
            for e in self._access
            if e.tenant_id == tenant_id and (since is None or e.created_at >= since)
        ]
        events = events[offset:]
        if limit is not None:
            events = events[:limit]
        return [e.model_copy(deep=True) for e in events]

    def delete_events_before(
        self, *, cutoff: datetime, tenant_id: str, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> int:
        ctx.require(tenant_id)
        login_count = len(self._logins)
        self._logins = [
            event
            for event in self._logins
            if not (event.tenant_id == tenant_id and event.created_at < cutoff)
        ]
        access_count = len(self._access)
        self._access = [
            event
            for event in self._access
            if not (event.tenant_id == tenant_id and event.created_at < cutoff)
        ]
        return (login_count - len(self._logins)) + (access_count - len(self._access))

    def purge_tenant(self, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT) -> int:
        ctx.require(tenant_id)
        key_ids = [
            key_id
            for key_id, record in self._keys.items()
            if record.tenant_id == tenant_id
        ]
        for key_id in key_ids:
            del self._keys[key_id]
        login_count = len(self._logins)
        self._logins = [e for e in self._logins if e.tenant_id != tenant_id]
        access_count = len(self._access)
        self._access = [e for e in self._access if e.tenant_id != tenant_id]
        return (
            len(key_ids)
            + (login_count - len(self._logins))
            + (access_count - len(self._access))
        )

    def clear(self) -> None:
        self._keys.clear()
        self._logins.clear()
        self._access.clear()


class PostgresAuthStore:
    """A durable auth store backed by PostgreSQL (M45)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def save_key(self, record: ApiKeyRecord, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(record.tenant_id)
        with self._session.begin() as session:
            row = session.get(ApiKeyRow, record.key_id)
            payload = record.model_dump(mode="json")
            if row is None:
                session.add(
                    ApiKeyRow(
                        key_id=record.key_id,
                        tenant_id=record.tenant_id,
                        role=record.role.value,
                        hashed_key=record.hashed_key,
                        revoked_at=record.revoked_at,
                        created_at=record.created_at,
                        payload=payload,
                    )
                )
            else:
                row.revoked_at = record.revoked_at
                row.payload = payload

    def get_key(self, key_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT) -> ApiKeyRecord | None:
        with self._session() as session:
            row = session.get(ApiKeyRow, key_id)
            if row is None or not ctx.scopes(row.tenant_id):
                return None
            return ApiKeyRecord.model_validate(row.payload)

    def find_key_by_hash(self, hashed_key: str) -> ApiKeyRecord | None:
        with self._session() as session:
            row = session.scalars(
                select(ApiKeyRow).where(ApiKeyRow.hashed_key == hashed_key)
            ).first()
            if row is None:
                return None
            return ApiKeyRecord.model_validate(row.payload)

    def list_keys(
        self, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[ApiKeyRecord]:
        if not ctx.scopes(tenant_id):
            return []
        with self._session() as session:
            rows = session.scalars(
                select(ApiKeyRow)
                .where(ApiKeyRow.tenant_id == tenant_id)
                .order_by(ApiKeyRow.key_id)
            ).all()
        return [ApiKeyRecord.model_validate(row.payload) for row in rows]

    def save_login(self, event: LoginEvent, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(event.tenant_id)
        self._append(
            kind="login",
            tenant_id=event.tenant_id,
            actor=event.actor,
            method=event.method.value,
            action="login",
            result=event.result.value,
            created_at=event.created_at,
            payload=event.model_dump(mode="json"),
        )

    def list_logins(
        self,
        tenant_id: str,
        *,
        since: datetime | None = None,
        limit: int | None = None,
        offset: int = 0,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[LoginEvent]:
        if not ctx.scopes(tenant_id):
            return []
        return [
            LoginEvent.model_validate(row.payload)
            for row in self._rows(tenant_id, "login", since=since, limit=limit, offset=offset)
        ]

    def save_access(self, event: AccessEvent, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        ctx.require(event.tenant_id)
        self._append(
            kind="access",
            tenant_id=event.tenant_id,
            actor=event.actor,
            method=event.method.value,
            action=event.action,
            result=event.result.value,
            created_at=event.created_at,
            payload=event.model_dump(mode="json"),
        )

    def list_access(
        self,
        tenant_id: str,
        *,
        since: datetime | None = None,
        limit: int | None = None,
        offset: int = 0,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[AccessEvent]:
        if not ctx.scopes(tenant_id):
            return []
        return [
            AccessEvent.model_validate(row.payload)
            for row in self._rows(tenant_id, "access", since=since, limit=limit, offset=offset)
        ]

    def delete_events_before(
        self, *, cutoff: datetime, tenant_id: str, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> int:
        ctx.require(tenant_id)
        with self._session.begin() as session:
            result = cast("CursorResult[Any]", session.execute(
                delete(AccessAuditRow).where(
                    AccessAuditRow.tenant_id == tenant_id,
                    AccessAuditRow.created_at < cutoff,
                )
            ))
            return int(result.rowcount or 0)

    def purge_tenant(self, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT) -> int:
        ctx.require(tenant_id)
        with self._session.begin() as session:
            total = 0
            for table in (ApiKeyRow, AccessAuditRow):
                result = cast(
                    "CursorResult[Any]",
                    session.execute(
                        delete(table).where(table.tenant_id == tenant_id)
                    ),
                )
                total += int(result.rowcount or 0)
            return total

    def clear(self) -> None:
        with self._session.begin() as session:
            session.execute(delete(AccessAuditRow))
            session.execute(delete(ApiKeyRow))

    def _append(
        self,
        *,
        kind: str,
        tenant_id: str,
        actor: str,
        method: str,
        action: str,
        result: str,
        created_at: datetime,
        payload: dict[str, object],
    ) -> None:
        with self._session.begin() as session:
            session.add(
                AccessAuditRow(
                    tenant_id=tenant_id,
                    kind=kind,
                    actor=actor,
                    method=method,
                    action=action,
                    result=result,
                    created_at=created_at,
                    payload=payload,
                )
            )

    def _rows(
        self,
        tenant_id: str,
        kind: str,
        *,
        since: datetime | None = None,
        limit: int | None = None,
        offset: int = 0,
    ) -> list[AccessAuditRow]:
        statement = select(AccessAuditRow).where(
            AccessAuditRow.tenant_id == tenant_id,
            AccessAuditRow.kind == kind,
        )
        if since is not None:
            statement = statement.where(AccessAuditRow.created_at >= since)
        statement = statement.order_by(AccessAuditRow.id).offset(offset)
        if limit is not None:
            statement = statement.limit(limit)
        with self._session() as session:
            return list(session.scalars(statement).all())


def build_auth_store(settings: Settings | None = None) -> AuthStore:
    """Build the configured auth store (in-memory by default)."""
    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresAuthStore(create_engine_from_settings(resolved))
    return InMemoryAuthStore()
