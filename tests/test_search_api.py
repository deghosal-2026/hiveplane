"""Tests for the control-plane global search endpoint (M52-07)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import cast

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
from hiveplane.core.approval import ApprovalRecord
from hiveplane.core.run import AdmissionContext
from hiveplane.core.workload import AgentWorkload
from hiveplane.tenancy import TenantContext
from hiveplane.tenancy.context import SYSTEM_CONTEXT


@pytest.fixture
def client(make_manifest: Callable[..., AgentWorkload]) -> TestClient:
    """Build an app seeded with a matching workload, run, and approval."""
    app = create_app()
    registry = app.state.registry_service
    runs = app.state.run_service
    registry.create(make_manifest(name="agent-search"))
    run = runs.submit(
        workload="agent-search", caller="cli", context=AdmissionContext.SANDBOX
    )
    app.state.approval_service.request(
        run_id=run.id,
        workload="agent-search",
        rule="approvals.required",
        reason="needs review",
    )
    return TestClient(app)


def test_search_matches_runs_approvals_workloads(client: TestClient) -> None:
    hits = client.get("/search", params={"q": "agent"}).json()

    assert {h["kind"] for h in hits} <= {"run", "approval", "workload", "attestation"}
    assert all("identifier" in h for h in hits)
    assert {h["kind"] for h in hits} == {"run", "approval", "workload"}


def test_search_empty_query_returns_no_hits(client: TestClient) -> None:
    assert client.get("/search", params={"q": "   "}).json() == []
    assert client.get("/search").json() == []


def test_search_no_match_returns_no_hits(client: TestClient) -> None:
    assert client.get("/search", params={"q": "zzz-missing"}).json() == []


def test_search_limit_caps_hits(client: TestClient) -> None:
    hits = client.get("/search", params={"q": "agent", "limit": 1}).json()

    assert len(hits) == 1


def test_search_requires_authentication_when_auth_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HIVEPLANE_AUTH__ENABLED", "true")
    from hiveplane.config import get_settings

    get_settings.cache_clear()
    client = TestClient(create_app())

    assert client.get("/search", params={"q": "agent"}).status_code == 401


def test_search_run_hits_are_tenant_scoped(
    client: TestClient, make_manifest: Callable[..., AgentWorkload]
) -> None:
    app = cast("FastAPI", client.app)
    app.state.tenant_admin_service.create_tenant(
        SYSTEM_CONTEXT, tenant_id="other", name="Other"
    )
    app.state.registry_service.create(
        make_manifest(name="agent-search-other"), ctx=TenantContext(tenant_id="other")
    )
    app.state.run_service.submit(
        workload="agent-search-other",
        caller="tenant-b-marker",
        context=AdmissionContext.SANDBOX,
        ctx=TenantContext(tenant_id="other"),
    )

    local = client.get("/search", params={"q": "tenant-b-marker"}).json()
    other = client.get(
        "/search",
        params={"q": "tenant-b-marker"},
        headers={"X-Hiveplane-Tenant": "other"},
    ).json()

    assert local == []
    assert [hit["kind"] for hit in other] == ["run"]


def test_search_approval_hits_are_tenant_scoped(client: TestClient) -> None:
    app = cast("FastAPI", client.app)
    app.state.approval_service._store.save(
        ApprovalRecord(
            approval_id="ap-other",
            run_id="run-other",
            workload="agent-search",
            rule="approvals.required",
            reason="tenant-b approval marker",
            requested_at=datetime.now(UTC),
            tenant_id="other",
        ),
        ctx=TenantContext(tenant_id="other"),
    )

    local = client.get("/search", params={"q": "tenant-b approval marker"}).json()
    other = client.get(
        "/search",
        params={"q": "tenant-b approval marker"},
        headers={"X-Hiveplane-Tenant": "other"},
    ).json()

    assert local == []
    assert [hit["kind"] for hit in other] == ["approval"]


def test_search_workload_hits_are_tenant_scoped(client: TestClient) -> None:
    local = client.get("/search", params={"q": "agent-search"}).json()
    other = client.get(
        "/search",
        params={"q": "agent-search"},
        headers={"X-Hiveplane-Tenant": "other"},
    ).json()

    assert any(hit["kind"] == "workload" for hit in local)
    assert not any(hit["kind"] == "workload" for hit in other)


def test_search_hit_shape(client: TestClient) -> None:
    hits = client.get("/search", params={"q": "agent"}).json()

    workload_hit = next(h for h in hits if h["kind"] == "workload")
    assert workload_hit["identifier"] == "agent-search"
    assert workload_hit["label"] == "platform-team"
