"""Tests for the registry service (M3: CRUD, catalog, dry-run)."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from hiveplane.certification.models import CertificationStatus
from hiveplane.core.spec import RuntimeAdapter
from hiveplane.core.workload import AgentWorkload
from hiveplane.registry.errors import (
    WorkloadAlreadyExistsError,
    WorkloadNotFoundError,
)
from hiveplane.registry.models import WorkloadRecord
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore

ManifestFactory = Callable[..., AgentWorkload]


def _names(records: list[WorkloadRecord]) -> list[str]:
    return [record.name for record in records]


@pytest.fixture
def service() -> RegistryService:
    return RegistryService(InMemoryRegistryStore())


def test_create_workload_persists_version_one(
    service: RegistryService, make_manifest: ManifestFactory
) -> None:
    record = service.create(make_manifest("repo-agent"))

    assert record.name == "repo-agent"
    assert record.current_version == 1
    assert record.certification_status is CertificationStatus.UNCERTIFIED
    assert service.get("repo-agent") == record
    assert len(service.versions("repo-agent")) == 1


def test_create_duplicate_is_rejected(
    service: RegistryService, make_manifest: ManifestFactory
) -> None:
    service.create(make_manifest("repo-agent"))

    with pytest.raises(WorkloadAlreadyExistsError):
        service.create(make_manifest("repo-agent"))


def test_get_missing_raises(service: RegistryService) -> None:
    with pytest.raises(WorkloadNotFoundError):
        service.get("missing")


def test_update_creates_a_new_version(
    service: RegistryService, make_manifest: ManifestFactory
) -> None:
    service.create(make_manifest("repo-agent"))
    updated = service.update("repo-agent", make_manifest("repo-agent", owner="new-team"))

    assert updated.current_version == 2
    assert updated.owner == "new-team"
    assert [v.version for v in service.versions("repo-agent")] == [1, 2]


def test_delete_removes_the_workload(
    service: RegistryService, make_manifest: ManifestFactory
) -> None:
    service.create(make_manifest("repo-agent"))
    service.delete("repo-agent")

    with pytest.raises(WorkloadNotFoundError):
        service.get("repo-agent")


def test_list_filters_by_owner_team_runtime_and_status(
    service: RegistryService, make_manifest: ManifestFactory
) -> None:
    service.create(make_manifest("repo-agent", owner="platform-team", team="platform"))
    service.create(
        make_manifest("docs-agent", owner="docs-team", team="dx", adapter="langgraph")
    )

    assert {r.name for r in service.list_workloads()} == {"repo-agent", "docs-agent"}
    assert _names(service.list_workloads(owner="docs-team")) == ["docs-agent"]
    assert _names(service.list_workloads(team="platform")) == ["repo-agent"]
    assert _names(service.list_workloads(runtime=RuntimeAdapter.LANGGRAPH)) == ["docs-agent"]
    assert _names(service.list_workloads(runtime="raw-worker")) == ["repo-agent"]
    assert _names(service.list_workloads(certification_status="uncertified")) == [
        "docs-agent",
        "repo-agent",
    ]
    with pytest.raises(ValueError, match="certification_status"):
        service.list_workloads(certification_status="bogus")


def test_list_pagination_and_sorting(
    service: RegistryService, make_manifest: ManifestFactory
) -> None:
    for name in ("alpha", "bravo", "charlie"):
        service.create(make_manifest(name))

    assert _names(service.list_workloads(sort="name", limit=2, offset=1)) == [
        "bravo",
        "charlie",
    ]
    assert _names(service.list_workloads(sort="name", descending=True, limit=1)) == ["charlie"]
    with pytest.raises(ValueError, match="sort"):
        service.list_workloads(sort="bogus")


def test_catalog_includes_run_and_failure_summary(
    service: RegistryService, make_manifest: ManifestFactory
) -> None:
    service.create(make_manifest("repo-agent"))
    record = service.get("repo-agent")

    assert record.last_run_at is None
    assert record.failure_count == 0
    assert record.last_failure_at is None


def test_dry_run_returns_summary_and_does_not_persist(
    service: RegistryService, make_manifest: ManifestFactory
) -> None:
    summary = service.create(make_manifest("repo-agent"), dry_run=True)

    assert summary.valid is True
    assert summary.name == "repo-agent"
    assert summary.runtime is RuntimeAdapter.RAW_WORKER
    assert summary.production_admitted is False
    assert "verification_required" not in summary.warnings
    with pytest.raises(WorkloadNotFoundError):
        service.get("repo-agent")


def test_dry_run_matches_real_write_for_valid_manifest(
    service: RegistryService, make_manifest: ManifestFactory
) -> None:
    manifest = make_manifest("repo-agent")
    summary = service.create(manifest, dry_run=True)
    record = service.create(manifest)

    assert summary.certification_status == record.certification_status
    assert summary.owner == record.owner


def test_dry_run_reports_production_admission_for_certified(
    service: RegistryService, make_manifest: ManifestFactory
) -> None:
    summary = service.create(
        make_manifest(
            "repo-agent",
            certification={
                "benchmark_corpus": "corpora/x/v1",
                "staging_threshold": 0.8,
                "production_threshold": 0.9,
                "status": "certified",
                "attestation_id": "att-1",
                "expires_at": "2999-01-01T00:00:00Z",
            },
        ),
        dry_run=True,
    )

    assert summary.production_admitted is True
