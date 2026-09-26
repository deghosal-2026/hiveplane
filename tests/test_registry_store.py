"""Focused tests for the registry store (memory + Postgres) edge paths."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from sqlalchemy import Engine

from hiveplane.certification.models import Attestation
from hiveplane.certification.signing import generate_keypair, sign_attestation
from hiveplane.core.tools import ToolTrustLevel
from hiveplane.core.workload import AgentWorkload
from hiveplane.registry.errors import WorkloadNotFoundError
from hiveplane.registry.models import ToolRecord, WorkloadVersion
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore, PostgresRegistryStore
from postgres import ensure_schema

_NOW = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)


def _store(make_manifest: Callable[..., AgentWorkload]) -> tuple[InMemoryRegistryStore, Any]:
    store = InMemoryRegistryStore()
    service = RegistryService(store, clock=lambda: _NOW)
    service.create(make_manifest(name="repo-agent"))
    return store, service


def _attestation(attestation_id: str = "att-x") -> Attestation:
    return Attestation.model_validate(
        {
            "attestation_id": attestation_id,
            "workload_id": "repo-agent",
            "manifest_version": 1,
            "benchmark_version": "1.0.0",
            "benchmark_run_id": "br-1",
            "corpus_id": "corpus",
            "corpus_version": 1,
            "model_identity": "gpt-4o-2024-08-06",
            "status": "certified",
            "target_context": "production",
            "eval_summary": {
                "pass_rate": 0.95,
                "critical_failures": 0,
                "p95_latency_ms": 1000,
                "tasks_passed": 19,
                "tasks_failed": 1,
            },
            "timestamp": "2026-09-12T10:00:00Z",
            "environment": {
                "sandbox_image": "img",
                "runtime_adapter": "raw-worker",
                "control_plane_version": "0.2.0",
            },
            "signer": {
                "identity": "cert@hiveplane",
                "key_id": "key-1",
                "signature": "unsigned",
            },
        }
    )


def test_missing_lookups_return_empty(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    store, _ = _store(make_manifest)

    assert store.get_workload("ghost") is None
    assert store.delete_workload("ghost") is False
    assert store.list_versions("ghost") == []
    assert store.get_version("ghost", 1) is None
    assert store.list_triggers("ghost") == []
    assert store.delete_trigger("ghost", "t1") is False
    assert store.get_attestation("ghost") is None
    assert store.get_tool("ghost") is None
    assert store.list_tools() == []


def test_add_version_requires_an_existing_parent(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    store = InMemoryRegistryStore()
    version = WorkloadVersion(
        workload="ghost", version=1, manifest=make_manifest(name="ghost"), created_at=_NOW
    )

    with pytest.raises(WorkloadNotFoundError):
        store.add_version(version)


def test_delete_workload_cascades_versions(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    store, _ = _store(make_manifest)

    assert store.delete_workload("repo-agent") is True

    assert store.list_workloads() == []
    assert store.list_versions("repo-agent") == []
    assert store.get_version("repo-agent", 1) is None


def test_attestation_and_tool_round_trip(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    store, _ = _store(make_manifest)
    private_key, _ = generate_keypair()
    attestation = sign_attestation(_attestation(), private_key)

    store.add_attestation(attestation)
    tool = ToolRecord(
        tool_id="mcp-1",
        name="MCP",
        mcp_server="srv",
        trust_level=ToolTrustLevel.READ_ONLY,
        registered_at=_NOW,
        registered_by="tester",
    )
    store.save_tool(tool)

    assert store.get_attestation("att-x") is not None
    assert store.list_attestations("repo-agent")[0].attestation_id == "att-x"
    assert store.list_attestations("ghost") == []
    assert store.get_tool("mcp-1") is not None
    assert store.list_tools()[0].tool_id == "mcp-1"


def test_postgres_bundle_round_trip(
    pg_engine: Engine, make_manifest: Callable[..., AgentWorkload]
) -> None:
    ensure_schema(pg_engine)
    private_key, public_key = generate_keypair()
    service = RegistryService(
        PostgresRegistryStore(pg_engine),
        clock=lambda: _NOW,
        attestation_public_key=public_key,
        bundle_signing_key=private_key,
    )
    with contextlib.suppress(WorkloadNotFoundError):
        service.delete("pg-agent-m35")
    service.create(make_manifest(name="pg-agent-m35"))

    reopened = PostgresRegistryStore(pg_engine)
    record = reopened.get_workload("pg-agent-m35")

    assert record is not None
    assert record.bundle is not None
    assert record.bundle.key_id
    assert service.versions("pg-agent-m35")[0].version == 1
    service.delete("pg-agent-m35")
    assert reopened.get_workload("pg-agent-m35") is None
