"""Persistence for artifact metadata and retention policies (M54)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, cast

from sqlalchemy import Engine, delete, select
from sqlalchemy.engine import CursorResult

from hiveplane.config import Settings, get_settings
from hiveplane.fleet.artifacts import Artifact, RetentionPolicy
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import ArtifactRow, RetentionPolicyRow
from hiveplane.tenancy.context import DEFAULT_CONTEXT, TenantContext
from hiveplane.tenancy.errors import TenantScopeError


class ArtifactStore(Protocol):
    """Storage interface for artifact metadata and retention policies."""

    def save_artifact(
        self, artifact: Artifact, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def get_artifact(
        self, artifact_id: str, *, tenant_id: str, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> Artifact | None: ...

    def list_artifacts(
        self,
        *,
        tenant_id: str,
        run_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[Artifact]: ...

    def expired(
        self,
        *,
        now: datetime,
        tenant_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[Artifact]: ...

    def delete_artifact(
        self, artifact_id: str, *, tenant_id: str, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def save_retention(
        self, policy: RetentionPolicy, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def get_retention(
        self, policy_id: str, *, tenant_id: str, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> RetentionPolicy | None: ...

    def list_retention(
        self, *, tenant_id: str, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[RetentionPolicy]: ...

    def retention_tenants(self) -> list[str]: ...

    def purge_tenant(self, tenant_id: str) -> int: ...

    def clear(self) -> None: ...


class InMemoryArtifactStore:
    """A process-local artifact metadata store."""

    def __init__(self) -> None:
        self._artifacts: dict[tuple[str, str], Artifact] = {}
        self._retention: dict[tuple[str, str], RetentionPolicy] = {}

    def save_artifact(
        self, artifact: Artifact, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(artifact.tenant_id)
        self._artifacts[(artifact.tenant_id, artifact.artifact_id)] = artifact.model_copy(
            deep=True
        )

    def get_artifact(
        self, artifact_id: str, *, tenant_id: str, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> Artifact | None:
        if not ctx.scopes(tenant_id):
            return None
        record = self._artifacts.get((tenant_id, artifact_id))
        return None if record is None else record.model_copy(deep=True)

    def list_artifacts(
        self,
        *,
        tenant_id: str,
        run_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[Artifact]:
        if not ctx.scopes(tenant_id):
            return []
        records = [
            record.model_copy(deep=True)
            for (scope, _), record in self._artifacts.items()
            if scope == tenant_id and (run_id is None or record.run_id == run_id)
        ]
        records.sort(key=lambda record: (record.created_at, record.artifact_id))
        return records

    def expired(
        self,
        *,
        now: datetime,
        tenant_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[Artifact]:
        if tenant_id is not None and not ctx.scopes(tenant_id):
            return []
        scope = tenant_id if tenant_id is not None else (None if ctx.is_system else ctx.tenant_id)
        records = [
            record.model_copy(deep=True)
            for (owner, _), record in self._artifacts.items()
            if (scope is None or owner == scope)
            and record.expires_at is not None
            and record.expires_at <= now
        ]
        records.sort(key=lambda record: (record.expires_at, record.artifact_id))
        return records

    def delete_artifact(
        self, artifact_id: str, *, tenant_id: str, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        if not ctx.scopes(tenant_id):
            return
        self._artifacts.pop((tenant_id, artifact_id), None)

    def save_retention(
        self, policy: RetentionPolicy, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(policy.tenant_id)
        self._retention[(policy.tenant_id, policy.policy_id)] = policy.model_copy(
            deep=True
        )

    def get_retention(
        self, policy_id: str, *, tenant_id: str, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> RetentionPolicy | None:
        if not ctx.scopes(tenant_id):
            return None
        record = self._retention.get((tenant_id, policy_id))
        return None if record is None else record.model_copy(deep=True)

    def list_retention(
        self, *, tenant_id: str, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[RetentionPolicy]:
        if not ctx.scopes(tenant_id):
            return []
        records = [
            record.model_copy(deep=True)
            for (scope, _), record in self._retention.items()
            if scope == tenant_id
        ]
        records.sort(key=lambda record: record.policy_id)
        return records

    def retention_tenants(self) -> list[str]:
        """Return every tenant that has a persisted retention policy."""
        return sorted({tenant_id for tenant_id, _ in self._retention})

    def purge_tenant(self, tenant_id: str) -> int:
        """Delete a tenant's artifact metadata and retention policies."""
        artifact_keys = [
            key for key in self._artifacts if key[0] == tenant_id
        ]
        for key in artifact_keys:
            del self._artifacts[key]
        policy_keys = [key for key in self._retention if key[0] == tenant_id]
        for key in policy_keys:
            del self._retention[key]
        return len(artifact_keys) + len(policy_keys)

    def clear(self) -> None:
        self._artifacts.clear()
        self._retention.clear()


class PostgresArtifactStore:
    """A durable artifact metadata store backed by PostgreSQL (M54)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def save_artifact(
        self, artifact: Artifact, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(artifact.tenant_id)
        with self._session.begin() as session:
            row = session.get(ArtifactRow, artifact.artifact_id)
            payload = artifact.model_dump(mode="json")
            if row is None:
                session.add(
                    ArtifactRow(
                        artifact_id=artifact.artifact_id,
                        run_id=artifact.run_id,
                        location=artifact.location,
                        size_bytes=artifact.size_bytes,
                        content_hash=artifact.content_hash,
                        retention_policy_id=artifact.retention_policy_id,
                        created_at=artifact.created_at,
                        expires_at=artifact.expires_at,
                        tenant_id=artifact.tenant_id,
                        payload=payload,
                    )
                )
            else:
                if row.tenant_id != artifact.tenant_id:
                    raise TenantScopeError(
                        ctx.tenant_id, f"cannot modify artifact {artifact.artifact_id!r}"
                    )
                row.run_id = artifact.run_id
                row.location = artifact.location
                row.size_bytes = artifact.size_bytes
                row.content_hash = artifact.content_hash
                row.retention_policy_id = artifact.retention_policy_id
                row.created_at = artifact.created_at
                row.expires_at = artifact.expires_at
                row.tenant_id = artifact.tenant_id
                row.payload = payload

    def get_artifact(
        self, artifact_id: str, *, tenant_id: str, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> Artifact | None:
        if not ctx.scopes(tenant_id):
            return None
        with self._session() as session:
            row = session.get(ArtifactRow, artifact_id)
            if row is None or row.tenant_id != tenant_id:
                return None
            return Artifact.model_validate(row.payload)

    def list_artifacts(
        self,
        *,
        tenant_id: str,
        run_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[Artifact]:
        if not ctx.scopes(tenant_id):
            return []
        statement = select(ArtifactRow).where(ArtifactRow.tenant_id == tenant_id)
        if run_id is not None:
            statement = statement.where(ArtifactRow.run_id == run_id)
        statement = statement.order_by(ArtifactRow.created_at, ArtifactRow.artifact_id)
        with self._session() as session:
            return [
                Artifact.model_validate(row.payload) for row in session.scalars(statement)
            ]

    def expired(
        self,
        *,
        now: datetime,
        tenant_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[Artifact]:
        if tenant_id is not None and not ctx.scopes(tenant_id):
            return []
        statement = select(ArtifactRow).where(
            ArtifactRow.expires_at.is_not(None), ArtifactRow.expires_at <= now
        )
        scope = tenant_id if tenant_id is not None else (None if ctx.is_system else ctx.tenant_id)
        if scope is not None:
            statement = statement.where(ArtifactRow.tenant_id == scope)
        statement = statement.order_by(ArtifactRow.expires_at, ArtifactRow.artifact_id)
        with self._session() as session:
            return [
                Artifact.model_validate(row.payload) for row in session.scalars(statement)
            ]

    def delete_artifact(
        self, artifact_id: str, *, tenant_id: str, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        if not ctx.scopes(tenant_id):
            return
        with self._session.begin() as session:
            row = session.get(ArtifactRow, artifact_id)
            if row is not None and row.tenant_id == tenant_id:
                session.delete(row)

    def save_retention(
        self, policy: RetentionPolicy, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(policy.tenant_id)
        with self._session.begin() as session:
            row = session.get(RetentionPolicyRow, policy.policy_id)
            if row is not None and row.tenant_id != policy.tenant_id:
                from hiveplane.artifacts.models import RetentionPolicyConflictError

                raise RetentionPolicyConflictError(policy.policy_id)
            payload = policy.model_dump(mode="json")
            if row is None:
                session.add(
                    RetentionPolicyRow(
                        policy_id=policy.policy_id,
                        data_class=policy.data_class,
                        retain_days=policy.retain_days,
                        legal_hold=policy.legal_hold,
                        tenant_id=policy.tenant_id,
                        payload=payload,
                    )
                )
            else:
                row.data_class = policy.data_class
                row.retain_days = policy.retain_days
                row.legal_hold = policy.legal_hold
                row.tenant_id = policy.tenant_id
                row.payload = payload

    def get_retention(
        self, policy_id: str, *, tenant_id: str, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> RetentionPolicy | None:
        if not ctx.scopes(tenant_id):
            return None
        with self._session() as session:
            row = session.get(RetentionPolicyRow, policy_id)
            if row is None or row.tenant_id != tenant_id:
                return None
            return RetentionPolicy.model_validate(row.payload)

    def list_retention(
        self, *, tenant_id: str, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[RetentionPolicy]:
        if not ctx.scopes(tenant_id):
            return []
        statement = (
            select(RetentionPolicyRow)
            .where(RetentionPolicyRow.tenant_id == tenant_id)
            .order_by(RetentionPolicyRow.policy_id)
        )
        with self._session() as session:
            return [
                RetentionPolicy.model_validate(row.payload)
                for row in session.scalars(statement)
            ]

    def retention_tenants(self) -> list[str]:
        """Return every tenant that has a persisted retention policy."""
        with self._session() as session:
            tenants = session.scalars(
                select(RetentionPolicyRow.tenant_id).distinct()
            ).all()
        return sorted(set(tenants))

    def purge_tenant(self, tenant_id: str) -> int:
        with self._session.begin() as session:
            total = 0
            for table in (ArtifactRow, RetentionPolicyRow):
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
            session.execute(delete(ArtifactRow))
            session.execute(delete(RetentionPolicyRow))


def build_artifact_store(settings: Settings | None = None) -> ArtifactStore:
    """Build the configured artifact metadata store (in-memory by default)."""
    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresArtifactStore(create_engine_from_settings(resolved))
    return InMemoryArtifactStore()
