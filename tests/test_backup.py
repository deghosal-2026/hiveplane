"""Backup and restore of control-plane state (M59-03)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from hiveplane.backup import (
    BackupIntegrityError,
    BackupSchemaMismatchError,
    BackupService,
    registry_store_target,
    run_store_target,
)
from hiveplane.certification.models import CertificationStatus
from hiveplane.core.run import Run, RunState
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.store import InMemoryRunStore
from hiveplane.registry.models import WorkloadRecord
from hiveplane.registry.store import InMemoryRegistryStore
from hiveplane.tenancy import Role
from hiveplane.tenancy.context import TenantContext

_NOW = datetime(2026, 9, 25, tzinfo=UTC)
_CTX_A = TenantContext(tenant_id="tenant-a", role=Role.ADMIN)


def _clock() -> datetime:
    return _NOW


def _run(run_id: str, tenant_id: str) -> Run:
    return Run(
        id=run_id,
        workload_id="agent-1",
        caller="alice",
        state=RunState.COMPLETED,
        created_at=_NOW,
        updated_at=_NOW,
        tenant_id=tenant_id,
    )


def _workload(tenant_id: str, make_manifest: Callable[..., AgentWorkload]) -> WorkloadRecord:
    manifest = make_manifest(name="agent-1")
    return WorkloadRecord(
        name=manifest.name,
        manifest=manifest,
        current_version=1,
        certification_status=CertificationStatus.CERTIFIED,
        owner=manifest.owner,
        team=manifest.team,
        runtime=manifest.spec.runtime.adapter,
        created_at=_NOW,
        updated_at=_NOW,
        tenant_id=tenant_id,
    )


def _seed(run_store: InMemoryRunStore) -> None:
    run_store.save_run(_run("r1", "tenant-a"), ctx=_CTX_A)


def test_backup_manifest_alembic_head_matches_migration_head() -> None:
    from pathlib import Path

    from alembic.config import Config
    from alembic.script import ScriptDirectory

    from hiveplane.backup.models import SCHEMA_HEAD

    root = Path(__file__).resolve().parents[1]
    config = Config()
    config.set_main_option(
        "script_location", str(root / "src/hiveplane/persistence/migrations")
    )
    assert ScriptDirectory.from_config(config).get_current_head() == SCHEMA_HEAD

    service = BackupService([run_store_target(InMemoryRunStore())], clock=_clock)
    assert service.create().manifest.alembic_head == SCHEMA_HEAD


def test_backup_restore_round_trip(make_manifest: Callable[..., AgentWorkload]) -> None:
    source_runs = InMemoryRunStore()
    source_registry = InMemoryRegistryStore()
    source_runs.save_run(_run("r1", "tenant-a"), ctx=_CTX_A)
    source_registry.save_workload(_workload("tenant-a", make_manifest), ctx=_CTX_A)

    source = BackupService(
        [run_store_target(source_runs), registry_store_target(source_registry)], clock=_clock
    )
    archive = source.create()
    assert source.verify(archive, source.public_key()) is True
    assert archive.manifest.total_records == 2

    target_runs = InMemoryRunStore()
    target_registry = InMemoryRegistryStore()
    target = BackupService(
        [run_store_target(target_runs), registry_store_target(target_registry)], clock=_clock
    )
    counts = target.restore(archive, source.public_key())

    assert target_runs.get_run("r1", ctx=_CTX_A) is not None
    assert target_registry.get_workload("agent-1", ctx=_CTX_A) is not None
    assert {count.target: count.restored for count in counts} == {"runs": 1, "workloads": 1}


def test_tampered_payload_fails_verification() -> None:
    runs = InMemoryRunStore()
    _seed(runs)
    service = BackupService([run_store_target(runs)], clock=_clock)
    archive = service.create()

    archive.payloads["runs"] = archive.payloads["runs"].replace("r1", "r2")

    assert service.verify(archive, service.public_key()) is False
    with pytest.raises(BackupIntegrityError):
        service.restore(archive, service.public_key())


def test_restore_refuses_schema_mismatch() -> None:
    runs = InMemoryRunStore()
    _seed(runs)
    service = BackupService([run_store_target(runs)], clock=_clock, schema_head="0034")
    archive = service.create()

    with pytest.raises(BackupSchemaMismatchError):
        service.restore(archive, service.public_key(), expected_head="0099")


def test_backup_is_deterministic() -> None:
    runs = InMemoryRunStore()
    _seed(runs)
    service = BackupService([run_store_target(runs)], clock=_clock)

    first = service.create()
    second = service.create()

    assert first.payloads == second.payloads
    assert [f.sha256 for f in first.manifest.files] == [f.sha256 for f in second.manifest.files]
    assert first.manifest.total_records == second.manifest.total_records


def test_wrong_public_key_rejects_restore() -> None:
    runs = InMemoryRunStore()
    _seed(runs)
    service = BackupService([run_store_target(runs)], clock=_clock)
    other = BackupService([run_store_target(InMemoryRunStore())], clock=_clock)
    archive = service.create()

    assert service.verify(archive, other.public_key()) is False
    with pytest.raises(BackupIntegrityError):
        service.restore(archive, other.public_key())


def test_restore_rejects_archive_signed_by_untrusted_key() -> None:
    from cryptography.hazmat.primitives import serialization
    from fastapi.testclient import TestClient

    from hiveplane.api.app import create_app

    untrusted = BackupService([run_store_target(InMemoryRunStore())], clock=_clock)
    archive = untrusted.create().model_dump(mode="json")
    untrusted_pem = untrusted.public_key().public_bytes(
        encoding=serialization.Encoding.PEM,
        format=serialization.PublicFormat.SubjectPublicKeyInfo,
    ).decode("ascii")

    client = TestClient(create_app())
    headers = {"X-Hiveplane-Tenant": "default"}

    foreign_key = client.post(
        "/backup/verify",
        headers=headers,
        json={"archive": archive, "public_key_pem": untrusted_pem},
    )
    assert foreign_key.status_code == 403

    refused = client.post(
        "/backup/restore",
        headers=headers,
        json={"archive": archive, "public_key_pem": untrusted_pem},
    )
    assert refused.status_code == 403

    plane_pem = client.get("/backup/public-key", headers=headers).json()["public_key_pem"]
    bad_signature = client.post(
        "/backup/verify",
        headers=headers,
        json={"archive": archive, "public_key_pem": plane_pem},
    )
    assert bad_signature.status_code == 200
    assert bad_signature.json()["valid"] is False

    restore_bad = client.post(
        "/backup/restore",
        headers=headers,
        json={"archive": archive, "public_key_pem": plane_pem},
    )
    assert restore_bad.status_code == 409


def test_backup_api_round_trip(make_manifest: Callable[..., AgentWorkload]) -> None:
    from fastapi.testclient import TestClient

    from hiveplane.api.app import create_app

    app = create_app()
    app.state.registry_service.create(make_manifest(name="agent-1"), ctx=_CTX_A)
    client = TestClient(app)
    headers = {"X-Hiveplane-Tenant": "default"}

    archive = client.post("/backup", headers=headers).json()
    pem = client.get("/backup/public-key", headers=headers).json()["public_key_pem"]

    verified = client.post(
        "/backup/verify", headers=headers, json={"archive": archive, "public_key_pem": pem}
    ).json()
    assert verified["valid"] is True

    archive["payloads"]["workloads"] = archive["payloads"]["workloads"].replace(
        "agent-1", "agent-x"
    )
    tampered = client.post(
        "/backup/verify", headers=headers, json={"archive": archive, "public_key_pem": pem}
    ).json()
    assert tampered["valid"] is False

    restored = client.post(
        "/backup/restore", headers=headers, json={"archive": archive, "public_key_pem": pem}
    )
    assert restored.status_code == 409
