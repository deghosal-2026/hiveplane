"""Storage for encrypted secrets (M45-01). In-memory and PostgreSQL."""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import Engine, delete, select

from hiveplane.config import Settings, get_settings
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import VaultSecretRow, VaultSecretVersionRow
from hiveplane.secrets.models import SecretRecord, SecretVersion
from hiveplane.tenancy.context import DEFAULT_CONTEXT, TenantContext
from hiveplane.tenancy.errors import TenantScopeError


class SecretStore(Protocol):
    """Storage interface for secrets and their versions."""

    def save_secret(
        self, record: SecretRecord, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def get_secret(
        self, tenant_id: str, name: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> SecretRecord | None: ...

    def list_secrets(
        self, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[SecretRecord]: ...

    def save_version(
        self, version: SecretVersion, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def get_version(
        self, secret_id: str, version: int, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> SecretVersion | None: ...

    def list_versions(
        self, secret_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[SecretVersion]: ...

    def clear(self) -> None: ...


class InMemorySecretStore:
    """A process-local secret store (ciphertext only)."""

    def __init__(self) -> None:
        self._secrets: dict[tuple[str, str], SecretRecord] = {}
        self._versions: dict[tuple[str, int], SecretVersion] = {}
        self._version_tenants: dict[tuple[str, int], str] = {}

    def save_secret(
        self, record: SecretRecord, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(record.tenant_id)
        self._secrets[(record.tenant_id, record.name)] = record.model_copy(deep=True)

    def get_secret(
        self, tenant_id: str, name: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> SecretRecord | None:
        if not ctx.scopes(tenant_id):
            return None
        record = self._secrets.get((tenant_id, name))
        return None if record is None else record.model_copy(deep=True)

    def list_secrets(
        self, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[SecretRecord]:
        if not ctx.scopes(tenant_id):
            return []
        records = [
            record.model_copy(deep=True)
            for (tenant, _), record in sorted(self._secrets.items())
            if tenant == tenant_id
        ]
        return records

    def save_version(
        self, version: SecretVersion, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(version.tenant_id)
        key = (version.secret_id, version.version)
        self._versions[key] = version.model_copy(deep=True)
        self._version_tenants[key] = version.tenant_id

    def get_version(
        self, secret_id: str, version: int, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> SecretVersion | None:
        key = (secret_id, version)
        if not ctx.scopes(self._version_tenants.get(key, "")):
            return None
        record = self._versions.get(key)
        return None if record is None else record.model_copy(deep=True)

    def list_versions(
        self, secret_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[SecretVersion]:
        records = [
            record.model_copy(deep=True)
            for (sid, version), record in sorted(self._versions.items())
            if sid == secret_id
            and ctx.scopes(self._version_tenants.get((sid, version), ""))
        ]
        return records

    def clear(self) -> None:
        self._secrets.clear()
        self._versions.clear()
        self._version_tenants.clear()


class PostgresSecretStore:
    """A durable secret store backed by PostgreSQL (M45-01)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def save_secret(
        self, record: SecretRecord, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(record.tenant_id)
        with self._session.begin() as session:
            row = session.get(VaultSecretRow, record.secret_id)
            payload = record.model_dump(mode="json")
            if row is None:
                session.add(
                    VaultSecretRow(
                        secret_id=record.secret_id,
                        tenant_id=record.tenant_id,
                        name=record.name,
                        current_version=record.current_version,
                        created_at=record.created_at,
                        rotated_at=record.rotated_at,
                        payload=payload,
                    )
                )
            else:
                if row.tenant_id != record.tenant_id:
                    raise TenantScopeError(
                        ctx.tenant_id, f"cannot modify secret {record.secret_id!r}"
                    )
                row.current_version = record.current_version
                row.rotated_at = record.rotated_at
                row.payload = payload

    def get_secret(
        self, tenant_id: str, name: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> SecretRecord | None:
        if not ctx.scopes(tenant_id):
            return None
        with self._session() as session:
            row = session.scalars(
                select(VaultSecretRow).where(
                    VaultSecretRow.tenant_id == tenant_id, VaultSecretRow.name == name
                )
            ).first()
            if row is None:
                return None
            return SecretRecord.model_validate(row.payload)

    def list_secrets(
        self, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[SecretRecord]:
        if not ctx.scopes(tenant_id):
            return []
        with self._session() as session:
            rows = session.scalars(
                select(VaultSecretRow)
                .where(VaultSecretRow.tenant_id == tenant_id)
                .order_by(VaultSecretRow.name)
            ).all()
        return [SecretRecord.model_validate(row.payload) for row in rows]

    def save_version(
        self, version: SecretVersion, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(version.tenant_id)
        with self._session.begin() as session:
            row = session.get(VaultSecretVersionRow, version.secret_id + f"@{version.version}")
            payload = version.model_dump(mode="json")
            row_id = f"{version.secret_id}@{version.version}"
            if row is None:
                session.add(
                    VaultSecretVersionRow(
                        version_id=row_id,
                        secret_id=version.secret_id,
                        tenant_id=version.tenant_id,
                        version=version.version,
                        created_at=version.created_at,
                        revoked_at=version.revoked_at,
                        payload=payload,
                    )
                )
            else:
                if row.tenant_id != version.tenant_id:
                    raise TenantScopeError(
                        ctx.tenant_id, f"cannot modify secret version {row_id!r}"
                    )
                row.revoked_at = version.revoked_at
                row.payload = payload

    def get_version(
        self, secret_id: str, version: int, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> SecretVersion | None:
        with self._session() as session:
            row = session.get(VaultSecretVersionRow, f"{secret_id}@{version}")
            if row is None or not ctx.scopes(row.tenant_id):
                return None
            return SecretVersion.model_validate(row.payload)

    def list_versions(
        self, secret_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[SecretVersion]:
        statement = select(VaultSecretVersionRow).where(
            VaultSecretVersionRow.secret_id == secret_id
        )
        if not ctx.is_system:
            statement = statement.where(VaultSecretVersionRow.tenant_id == ctx.tenant_id)
        statement = statement.order_by(VaultSecretVersionRow.version)
        with self._session() as session:
            rows = session.scalars(statement).all()
        return [SecretVersion.model_validate(row.payload) for row in rows]


    def clear(self) -> None:
        with self._session.begin() as session:
            session.execute(delete(VaultSecretVersionRow))
            session.execute(delete(VaultSecretRow))


def build_secret_store(settings: Settings | None = None) -> SecretStore:
    """Build the configured secret store (in-memory by default)."""
    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresSecretStore(create_engine_from_settings(resolved))
    return InMemorySecretStore()
