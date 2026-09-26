"""Storage for API keys and the access audit (M45-05..M45-07)."""

from __future__ import annotations

from datetime import datetime
from typing import Protocol

from sqlalchemy import Engine, delete, select

from hiveplane.auth.models import AccessEvent, ApiKeyRecord, LoginEvent
from hiveplane.config import Settings, get_settings
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import AccessAuditRow, ApiKeyRow


class AuthStore(Protocol):
    """Storage interface for API keys and access-audit events."""

    def save_key(self, record: ApiKeyRecord) -> None: ...

    def get_key(self, key_id: str) -> ApiKeyRecord | None: ...

    def find_key_by_hash(self, hashed_key: str) -> ApiKeyRecord | None: ...

    def list_keys(self, tenant_id: str) -> list[ApiKeyRecord]: ...

    def save_login(self, event: LoginEvent) -> None: ...

    def list_logins(self, tenant_id: str) -> list[LoginEvent]: ...

    def save_access(self, event: AccessEvent) -> None: ...

    def list_access(self, tenant_id: str) -> list[AccessEvent]: ...

    def clear(self) -> None: ...


class InMemoryAuthStore:
    """A process-local auth store."""

    def __init__(self) -> None:
        self._keys: dict[str, ApiKeyRecord] = {}
        self._logins: list[LoginEvent] = []
        self._access: list[AccessEvent] = []

    def save_key(self, record: ApiKeyRecord) -> None:
        self._keys[record.key_id] = record.model_copy(deep=True)

    def get_key(self, key_id: str) -> ApiKeyRecord | None:
        record = self._keys.get(key_id)
        return None if record is None else record.model_copy(deep=True)

    def find_key_by_hash(self, hashed_key: str) -> ApiKeyRecord | None:
        for record in self._keys.values():
            if record.hashed_key == hashed_key:
                return record.model_copy(deep=True)
        return None

    def list_keys(self, tenant_id: str) -> list[ApiKeyRecord]:
        records = [
            record.model_copy(deep=True)
            for _, record in sorted(self._keys.items())
            if record.tenant_id == tenant_id
        ]
        return records

    def save_login(self, event: LoginEvent) -> None:
        self._logins.append(event.model_copy(deep=True))

    def list_logins(self, tenant_id: str) -> list[LoginEvent]:
        return [e.model_copy(deep=True) for e in self._logins if e.tenant_id == tenant_id]

    def save_access(self, event: AccessEvent) -> None:
        self._access.append(event.model_copy(deep=True))

    def list_access(self, tenant_id: str) -> list[AccessEvent]:
        return [e.model_copy(deep=True) for e in self._access if e.tenant_id == tenant_id]

    def clear(self) -> None:
        self._keys.clear()
        self._logins.clear()
        self._access.clear()


class PostgresAuthStore:
    """A durable auth store backed by PostgreSQL (M45)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def save_key(self, record: ApiKeyRecord) -> None:
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

    def get_key(self, key_id: str) -> ApiKeyRecord | None:
        with self._session() as session:
            row = session.get(ApiKeyRow, key_id)
            if row is None:
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

    def list_keys(self, tenant_id: str) -> list[ApiKeyRecord]:
        with self._session() as session:
            rows = session.scalars(
                select(ApiKeyRow)
                .where(ApiKeyRow.tenant_id == tenant_id)
                .order_by(ApiKeyRow.key_id)
            ).all()
        return [ApiKeyRecord.model_validate(row.payload) for row in rows]

    def save_login(self, event: LoginEvent) -> None:
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

    def list_logins(self, tenant_id: str) -> list[LoginEvent]:
        return [
            LoginEvent.model_validate(row.payload) for row in self._rows(tenant_id, "login")
        ]

    def save_access(self, event: AccessEvent) -> None:
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

    def list_access(self, tenant_id: str) -> list[AccessEvent]:
        return [
            AccessEvent.model_validate(row.payload)
            for row in self._rows(tenant_id, "access")
        ]

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

    def _rows(self, tenant_id: str, kind: str) -> list[AccessAuditRow]:
        with self._session() as session:
            return list(
                session.scalars(
                    select(AccessAuditRow)
                    .where(
                        AccessAuditRow.tenant_id == tenant_id,
                        AccessAuditRow.kind == kind,
                    )
                    .order_by(AccessAuditRow.id)
                ).all()
            )


def build_auth_store(settings: Settings | None = None) -> AuthStore:
    """Build the configured auth store (in-memory by default)."""
    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresAuthStore(create_engine_from_settings(resolved))
    return InMemoryAuthStore()
