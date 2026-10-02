"""Tests for immutable corpus versioning and sharing (M55-03)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine

from hiveplane.config import Settings
from hiveplane.corpus.service import (
    CorpusImmutabilityError,
    CorpusService,
    corpus_content_hash,
)
from hiveplane.corpus.store import (
    InMemoryCorpusReleaseStore,
    PostgresCorpusReleaseStore,
    build_corpus_release_store,
)
from hiveplane.corpus.templates import TemplateKind, corpus_template
from hiveplane.tenancy import Role, TenantContext
from postgres import ensure_schema

_ACME = TenantContext(tenant_id="acme", role=Role.ADMIN)

_NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)


def _service() -> CorpusService:
    return CorpusService(InMemoryCorpusReleaseStore(), clock=lambda: _NOW)


def test_publish_is_immutable_and_idempotent() -> None:
    service = _service()
    corpus = corpus_template(TemplateKind.TRIAGE, corpus_id="triage")

    first = service.publish(corpus)
    again = service.publish(corpus)

    assert first == again
    assert first.content_hash == corpus_content_hash(corpus)
    assert first.created_at == _NOW


def test_republishing_changed_content_is_refused() -> None:
    service = _service()
    corpus = corpus_template(TemplateKind.TRIAGE, corpus_id="triage")
    service.publish(corpus)
    changed = corpus.model_copy(
        update={"tasks": corpus.tasks[:1]}
    )

    with pytest.raises(CorpusImmutabilityError):
        service.publish(changed)


def test_a_new_version_is_allowed() -> None:
    service = _service()
    corpus = corpus_template(TemplateKind.TRIAGE, corpus_id="triage")
    service.publish(corpus)

    bumped = service.publish(corpus.model_copy(update={"version": 2}))

    assert bumped.version == 2
    assert [release.version for release in service.releases(tenant_id="default")] == [1, 2]


def test_releases_are_tenant_scoped() -> None:
    service = _service()
    corpus = corpus_template(TemplateKind.TRIAGE, corpus_id="triage")
    service.publish(corpus, tenant_id="acme", ctx=_ACME)

    assert service.releases(tenant_id="other") == []
    with pytest.raises(CorpusImmutabilityError):
        service.get("triage", 1, tenant_id="other")


def test_export_document_round_trips_the_corpus() -> None:
    service = _service()
    corpus = corpus_template(TemplateKind.GENERATION, corpus_id="gen")
    service.publish(corpus)

    document = service.export_document("gen", 1, tenant_id="default")

    assert document["id"] == "gen"
    assert len(document["tasks"]) == len(corpus.tasks)


def test_builder_defaults_to_in_memory() -> None:
    assert isinstance(build_corpus_release_store(Settings()), InMemoryCorpusReleaseStore)


def test_builder_selects_postgres() -> None:
    settings = Settings.model_validate({"execution": {"store": "postgres"}})
    assert isinstance(build_corpus_release_store(settings), PostgresCorpusReleaseStore)


def test_postgres_release_round_trip(pg_engine: Engine) -> None:
    ensure_schema(pg_engine)
    store = PostgresCorpusReleaseStore(pg_engine)
    store.clear()
    service = CorpusService(store, clock=lambda: _NOW)
    corpus = corpus_template(TemplateKind.CLASSIFICATION, corpus_id="cls")

    release = service.publish(corpus)

    reopened = CorpusService(PostgresCorpusReleaseStore(pg_engine))
    assert reopened.get("cls", 1, tenant_id="default") == release
    assert [r.corpus_id for r in reopened.releases(tenant_id="default")] == ["cls"]
    store.clear()
