"""Persistence for published corpus releases (M55-03)."""

from __future__ import annotations

from typing import Protocol

from sqlalchemy import Engine, delete, select

from hiveplane.config import Settings, get_settings
from hiveplane.corpus.models import CorpusRelease
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import CorpusReleaseRow
from hiveplane.tenancy.context import DEFAULT_CONTEXT, TenantContext


class CorpusReleaseStore(Protocol):
    """Storage interface for published corpus releases."""

    def add(
        self, release: CorpusRelease, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def get(
        self,
        corpus_id: str,
        version: int,
        *,
        tenant_id: str,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> CorpusRelease | None: ...

    def list(
        self, *, tenant_id: str, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[CorpusRelease]: ...

    def clear(self) -> None: ...


class InMemoryCorpusReleaseStore:
    """A process-local corpus release store."""

    def __init__(self) -> None:
        self._releases: dict[tuple[str, str, int], CorpusRelease] = {}

    def add(
        self, release: CorpusRelease, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(release.tenant_id)
        key = (release.tenant_id, release.corpus_id, release.version)
        self._releases[key] = release.model_copy(deep=True)

    def get(
        self,
        corpus_id: str,
        version: int,
        *,
        tenant_id: str,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> CorpusRelease | None:
        if not ctx.scopes(tenant_id):
            return None
        record = self._releases.get((tenant_id, corpus_id, version))
        return None if record is None else record.model_copy(deep=True)

    def list(
        self, *, tenant_id: str, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[CorpusRelease]:
        if not ctx.scopes(tenant_id):
            return []
        records = [
            record.model_copy(deep=True)
            for (scope, _, _), record in self._releases.items()
            if scope == tenant_id
        ]
        records.sort(key=lambda record: (record.corpus_id, record.version))
        return records

    def clear(self) -> None:
        self._releases.clear()


class PostgresCorpusReleaseStore:
    """A durable corpus release store backed by PostgreSQL (M55-03)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def add(
        self, release: CorpusRelease, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(release.tenant_id)
        with self._session.begin() as session:
            statement = select(CorpusReleaseRow).where(
                CorpusReleaseRow.tenant_id == release.tenant_id,
                CorpusReleaseRow.corpus_id == release.corpus_id,
                CorpusReleaseRow.version == release.version,
            )
            row = session.scalars(statement).first()
            payload = release.model_dump(mode="json")
            if row is None:
                session.add(
                    CorpusReleaseRow(
                        release_id=f"{release.tenant_id}:{release.corpus_id}:v{release.version}",
                        corpus_id=release.corpus_id,
                        version=release.version,
                        content_hash=release.content_hash,
                        created_at=release.created_at,
                        tenant_id=release.tenant_id,
                        payload=payload,
                    )
                )
            else:
                row.content_hash = release.content_hash
                row.created_at = release.created_at
                row.payload = payload

    def get(
        self,
        corpus_id: str,
        version: int,
        *,
        tenant_id: str,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> CorpusRelease | None:
        if not ctx.scopes(tenant_id):
            return None
        statement = select(CorpusReleaseRow).where(
            CorpusReleaseRow.tenant_id == tenant_id,
            CorpusReleaseRow.corpus_id == corpus_id,
            CorpusReleaseRow.version == version,
        )
        with self._session() as session:
            row = session.scalars(statement).first()
            return None if row is None else CorpusRelease.model_validate(row.payload)

    def list(
        self, *, tenant_id: str, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[CorpusRelease]:
        if not ctx.scopes(tenant_id):
            return []
        statement = (
            select(CorpusReleaseRow)
            .where(CorpusReleaseRow.tenant_id == tenant_id)
            .order_by(CorpusReleaseRow.corpus_id, CorpusReleaseRow.version)
        )
        with self._session() as session:
            return [
                CorpusRelease.model_validate(row.payload)
                for row in session.scalars(statement)
            ]

    def clear(self) -> None:
        with self._session.begin() as session:
            session.execute(delete(CorpusReleaseRow))


def build_corpus_release_store(
    settings: Settings | None = None,
) -> CorpusReleaseStore:
    """Build the configured corpus release store (in-memory by default)."""
    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresCorpusReleaseStore(create_engine_from_settings(resolved))
    return InMemoryCorpusReleaseStore()
