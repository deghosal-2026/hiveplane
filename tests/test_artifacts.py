"""Tests for artifact capture, retention, and stores (M54-02/03)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import pytest
from sqlalchemy import Engine

from hiveplane.artifacts.backend import LocalBlobBackend
from hiveplane.artifacts.models import RetentionPolicyNotFoundError
from hiveplane.artifacts.service import (
    ArtifactNotFoundError,
    ArtifactService,
    RetentionService,
)
from hiveplane.artifacts.store import (
    InMemoryArtifactStore,
    PostgresArtifactStore,
    build_artifact_store,
)
from hiveplane.config import Settings
from hiveplane.fleet.artifacts import RetentionPolicy
from hiveplane.persistence.audit import InMemoryAuditLog
from hiveplane.persistence.base import session_factory
from hiveplane.persistence.models import RunRow
from hiveplane.tenancy import Role, TenantContext
from hiveplane.tenancy.errors import TenantScopeError
from postgres import ensure_schema, seed_workload

_ACME = TenantContext(tenant_id="acme", role=Role.ADMIN)

_NOW = datetime(2026, 9, 27, 12, 0, 0, tzinfo=UTC)


def _service(
    tmp_path: Path,
    *,
    audit: InMemoryAuditLog | None = None,
) -> ArtifactService:
    return ArtifactService(
        InMemoryArtifactStore(),
        LocalBlobBackend(tmp_path),
        audit=audit,
        clock=lambda: _NOW,
        artifact_id_factory=lambda: "art-1",
    )


def test_capture_is_content_addressed_linked_and_retrievable(tmp_path: Path) -> None:
    service = _service(tmp_path)

    artifact = service.capture(
        tenant_id="default", run_id="run-1", filename="report.txt", data=b"hello"
    )

    assert artifact.run_id == "run-1"
    assert artifact.size_bytes == 5
    assert artifact.content_hash.startswith("sha256:")
    assert artifact.location.startswith("file://")
    assert service.list(tenant_id="default", run_id="run-1") == [artifact]
    stored_artifact, data = service.data("art-1", tenant_id="default")
    assert stored_artifact == artifact
    assert data == b"hello"


def test_same_bytes_share_a_content_address(tmp_path: Path) -> None:
    store = InMemoryArtifactStore()
    counter = iter(["art-1", "art-2"])
    service = ArtifactService(
        store,
        LocalBlobBackend(tmp_path),
        clock=lambda: _NOW,
        artifact_id_factory=lambda: next(counter),
    )

    first = service.capture(
        tenant_id="default", run_id="run-1", filename="a.txt", data=b"same"
    )
    second = service.capture(
        tenant_id="default", run_id="run-1", filename="b.txt", data=b"same"
    )

    assert first.content_hash == second.content_hash
    assert first.artifact_id != second.artifact_id


def test_retention_policy_sets_expiry(tmp_path: Path) -> None:
    service = _service(tmp_path)
    RetentionService(service._store, service._backend, clock=lambda: _NOW).set_policy(
        RetentionPolicy(
            policy_id="default-7d", tenant_id="default", data_class="run_output", retain_days=7
        )
    )

    artifact = service.capture(
        tenant_id="default",
        run_id="run-1",
        filename="report.txt",
        data=b"hello",
        retention_policy_id="default-7d",
    )

    assert artifact.expires_at == _NOW + timedelta(days=7)


def test_legal_hold_policy_is_never_purged(tmp_path: Path) -> None:
    store = InMemoryArtifactStore()
    backend = LocalBlobBackend(tmp_path)
    service = ArtifactService(store, backend, clock=lambda: _NOW)
    retention = RetentionService(store, backend, clock=lambda: _NOW)
    retention.set_policy(
        RetentionPolicy(
            policy_id="hold",
            tenant_id="default",
            data_class="run_output",
            retain_days=0,
            legal_hold=True,
        )
    )

    artifact = service.capture(
        tenant_id="default",
        run_id="run-1",
        filename="report.txt",
        data=b"hello",
        retention_policy_id="hold",
    )

    result = retention.purge_due(tenant_id="default")
    assert result.purged == []
    assert result.skipped_legal_hold == [artifact.artifact_id]
    assert backend.exists(artifact.location) is True


def test_get_is_tenant_scoped(tmp_path: Path) -> None:
    service = _service(tmp_path)
    service.capture(tenant_id="default", run_id="run-1", filename="a", data=b"x")

    with pytest.raises(ArtifactNotFoundError):
        service.get("art-1", tenant_id="other")


def test_delete_removes_bytes_and_metadata(tmp_path: Path) -> None:
    service = _service(tmp_path)
    artifact = service.capture(
        tenant_id="default", run_id="run-1", filename="a", data=b"x"
    )
    backend = service._backend

    service.delete(artifact.artifact_id, tenant_id="default")

    assert backend.exists(artifact.location) is False
    assert service.list(tenant_id="default") == []


def test_capture_and_delete_are_audited(tmp_path: Path) -> None:
    audit = InMemoryAuditLog()
    service = _service(tmp_path, audit=audit)
    artifact = service.capture(
        tenant_id="default", run_id="run-1", filename="a", data=b"x"
    )
    service.delete(artifact.artifact_id, tenant_id="default")

    actions = [record.action for record in audit.records()]
    assert actions == ["artifact.stored", "artifact.deleted"]


def test_retention_purge_removes_expired_and_skips_legal_hold(
    tmp_path: Path,
) -> None:
    store = InMemoryArtifactStore()
    backend = LocalBlobBackend(tmp_path)
    service = ArtifactService(
        store,
        backend,
        clock=lambda: _NOW,
    )
    retention = RetentionService(store, backend, clock=lambda: _NOW)
    retention.set_policy(
        RetentionPolicy(
            policy_id="short",
            tenant_id="default",
            data_class="run_output",
            retain_days=0,
        )
    )
    retention.set_policy(
        RetentionPolicy(
            policy_id="hold",
            tenant_id="default",
            data_class="run_output",
            retain_days=0,
            legal_hold=True,
        )
    )
    expiring = service.capture(
        tenant_id="default",
        run_id="run-1",
        filename="a",
        data=b"expire",
        retention_policy_id="short",
    )
    held = service.capture(
        tenant_id="default",
        run_id="run-1",
        filename="b",
        data=b"hold",
        retention_policy_id="hold",
    )

    result = retention.purge_due(tenant_id="default")

    assert result.purged == [expiring.artifact_id]
    assert result.skipped_legal_hold == [held.artifact_id]
    assert backend.exists(expiring.location) is False
    assert backend.exists(held.location) is True
    assert store.get_artifact(held.artifact_id, tenant_id="default") is not None


def test_retention_purge_at_overrides_clock(tmp_path: Path) -> None:
    store = InMemoryArtifactStore()
    backend = LocalBlobBackend(tmp_path)
    service = ArtifactService(store, backend, clock=lambda: _NOW)
    retention = RetentionService(store, backend, clock=lambda: _NOW)
    retention.set_policy(
        RetentionPolicy(
            policy_id="short", tenant_id="default", data_class="run_output", retain_days=0
        )
    )
    artifact = service.capture(
        tenant_id="default",
        run_id="run-1",
        filename="a",
        data=b"expire",
        retention_policy_id="short",
    )

    early = retention.purge_due(tenant_id="default", at=_NOW - timedelta(days=1))
    assert early.purged == []
    assert backend.exists(artifact.location) is True

    due = retention.purge_due(tenant_id="default", at=_NOW)
    assert due.purged == [artifact.artifact_id]
    assert backend.exists(artifact.location) is False


def test_purge_leaves_audit_evidence(tmp_path: Path) -> None:
    audit = InMemoryAuditLog()
    store = InMemoryArtifactStore()
    backend = LocalBlobBackend(tmp_path)
    service = ArtifactService(store, backend, clock=lambda: _NOW)
    retention = RetentionService(store, backend, audit=audit, clock=lambda: _NOW)
    retention.set_policy(
        RetentionPolicy(
            policy_id="short", tenant_id="default", data_class="run_output", retain_days=0
        )
    )
    artifact = service.capture(
        tenant_id="default",
        run_id="run-1",
        filename="a",
        data=b"x",
        retention_policy_id="short",
    )

    retention.purge_due(tenant_id="default")

    actions = [record.action for record in audit.records()]
    assert "retention.policy.set" in actions
    assert "artifact.purged" in actions
    assert audit.records()[-1].subject == artifact.artifact_id


def test_builder_defaults_to_in_memory() -> None:
    assert isinstance(build_artifact_store(Settings()), InMemoryArtifactStore)


def test_builder_selects_postgres() -> None:
    settings = Settings.model_validate({"execution": {"store": "postgres"}})
    assert isinstance(build_artifact_store(settings), PostgresArtifactStore)


def _seed_run(engine: Engine, run_id: str = "run-1") -> None:
    seed_workload(engine)
    with session_factory(engine).begin() as session:
        if session.get(RunRow, run_id) is None:
            session.add(
                RunRow(
                    id=run_id,
                    workload_id="agent-1",
                    caller="cli",
                    state="completed",
                    created_at=_NOW,
                    updated_at=_NOW,
                    tenant_id="default",
                    payload={},
                )
            )


def test_postgres_artifact_round_trip(pg_engine: Engine) -> None:
    ensure_schema(pg_engine)
    _seed_run(pg_engine)
    store = PostgresArtifactStore(pg_engine)
    store.clear()
    service = ArtifactService(
        store,
        LocalBlobBackend(Path("/tmp/hiveplane-artifacts-test")),
        clock=lambda: _NOW,
        artifact_id_factory=lambda: "art-pg",
    )

    artifact = service.capture(
        tenant_id="default", run_id="run-1", filename="report.txt", data=b"payload"
    )

    reopened = PostgresArtifactStore(pg_engine)
    assert reopened.get_artifact("art-pg", tenant_id="default") == artifact
    assert reopened.get_artifact("art-pg", tenant_id="other") is None
    assert [a.artifact_id for a in reopened.list_artifacts(tenant_id="default")] == [
        "art-pg"
    ]
    store.clear()


def test_capture_with_unknown_policy_is_refused(tmp_path: Path) -> None:
    service = _service(tmp_path)

    with pytest.raises(RetentionPolicyNotFoundError):
        service.capture(
            tenant_id="default",
            run_id="run-1",
            filename="a",
            data=b"x",
            retention_policy_id="missing",
        )


def test_identical_captures_have_distinct_locations(tmp_path: Path) -> None:
    store = InMemoryArtifactStore()
    backend = LocalBlobBackend(tmp_path)
    counter = iter(["art-1", "art-2"])
    service = ArtifactService(
        store,
        backend,
        clock=lambda: _NOW,
        artifact_id_factory=lambda: next(counter),
    )

    first = service.capture(
        tenant_id="default", run_id="run-1", filename="a.txt", data=b"same"
    )
    second = service.capture(
        tenant_id="default", run_id="run-1", filename="a.txt", data=b"same"
    )

    assert first.location != second.location
    service.delete(first.artifact_id, tenant_id="default")
    assert service.data(second.artifact_id, tenant_id="default")[1] == b"same"


def test_postgres_retention_policy_is_tenant_exclusive(pg_engine: Engine) -> None:

    ensure_schema(pg_engine)
    store = PostgresArtifactStore(pg_engine)
    store.clear()
    store.save_retention(
        RetentionPolicy(
            policy_id="short",
            tenant_id="acme",
            data_class="run_output",
            retain_days=1,
        ),
        ctx=_ACME,
    )

    with pytest.raises(TenantScopeError):
        store.save_retention(
            RetentionPolicy(
                policy_id="short",
                tenant_id="other",
                data_class="run_output",
                retain_days=1,
            ),
            ctx=_ACME,
        )
    store.clear()
