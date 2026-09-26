"""Storage for cut corpus versions (M36-04)."""

from __future__ import annotations

import threading
from typing import Protocol

from sqlalchemy import Engine, delete, select

from hiveplane.config import Settings, get_settings
from hiveplane.learning.models import CorpusVersion
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import CorpusVersionRow
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext, TenantScopeError


class CorpusVersionStore(Protocol):
    """Storage interface for cut corpus versions."""

    def add_version(
        self, version: CorpusVersion, *, ctx: TenantContext = ...
    ) -> None: ...

    def get_version(
        self, corpus_id: str, version: int, *, ctx: TenantContext = ...
    ) -> CorpusVersion | None: ...

    def list_versions(
        self, corpus_id: str, *, ctx: TenantContext = ...
    ) -> list[CorpusVersion]: ...

    def clear(self) -> None: ...


class InMemoryCorpusVersionStore:
    """A process-local, thread-safe corpus-version store."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._versions: dict[str, tuple[CorpusVersion, str]] = {}

    def add_version(
        self, version: CorpusVersion, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(version.tenant_id)
        with self._lock:
            for key, (existing, _) in self._versions.items():
                if existing.corpus_id == version.corpus_id and existing.version == version.version:
                    self._versions[key] = (version.model_copy(deep=True), version.tenant_id)
                    return
            self._versions[version.corpus_version_id] = (
                version.model_copy(deep=True),
                version.tenant_id,
            )

    def get_version(
        self, corpus_id: str, version: int, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> CorpusVersion | None:
        with self._lock:
            for record, tenant in self._versions.values():
                if (
                    record.corpus_id == corpus_id
                    and record.version == version
                    and ctx.scopes(tenant)
                ):
                    return record.model_copy(deep=True)
            return None

    def list_versions(
        self, corpus_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[CorpusVersion]:
        with self._lock:
            records = [
                record
                for record, tenant in self._versions.values()
                if record.corpus_id == corpus_id and ctx.scopes(tenant)
            ]
        records.sort(key=lambda record: (record.version, record.corpus_version_id))
        return records

    def clear(self) -> None:
        with self._lock:
            self._versions.clear()


class PostgresCorpusVersionStore:
    """A durable corpus-version store backed by PostgreSQL (M36-04)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def add_version(
        self, version: CorpusVersion, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(version.tenant_id)
        payload = version.model_dump(mode="json")
        with self._session.begin() as session:
            row = session.scalars(
                select(CorpusVersionRow).where(
                    CorpusVersionRow.corpus_id == version.corpus_id,
                    CorpusVersionRow.version == version.version,
                    CorpusVersionRow.tenant_id == version.tenant_id,
                )
            ).first()
            if row is None:
                session.add(
                    CorpusVersionRow(
                        corpus_version_id=version.corpus_version_id,
                        corpus_id=version.corpus_id,
                        version=version.version,
                        created_at=version.created_at,
                        tenant_id=version.tenant_id,
                        payload=payload,
                    )
                )
            else:
                if not ctx.scopes(row.tenant_id):
                    raise TenantScopeError(
                        ctx.tenant_id,
                        f"cannot modify corpus version {version.corpus_version_id!r}",
                    )
                row.payload = payload

    def get_version(
        self, corpus_id: str, version: int, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> CorpusVersion | None:
        statement = select(CorpusVersionRow).where(
            CorpusVersionRow.corpus_id == corpus_id,
            CorpusVersionRow.version == version,
        )
        with self._session() as session:
            row = session.scalars(statement).first()
            if row is None or not ctx.scopes(row.tenant_id):
                return None
            return CorpusVersion.model_validate(row.payload)

    def list_versions(
        self, corpus_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[CorpusVersion]:
        statement = select(CorpusVersionRow).where(
            CorpusVersionRow.corpus_id == corpus_id
        )
        if not ctx.is_system:
            statement = statement.where(CorpusVersionRow.tenant_id == ctx.tenant_id)
        statement = statement.order_by(
            CorpusVersionRow.version, CorpusVersionRow.corpus_version_id
        )
        with self._session() as session:
            return [
                CorpusVersion.model_validate(row.payload)
                for row in session.scalars(statement)
            ]

    def clear(self) -> None:
        with self._session.begin() as session:
            session.execute(delete(CorpusVersionRow))


def build_corpus_version_store(settings: Settings | None = None) -> CorpusVersionStore:
    """Build the configured corpus-version store (in-memory by default)."""
    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresCorpusVersionStore(create_engine_from_settings(resolved))
    return InMemoryCorpusVersionStore()
