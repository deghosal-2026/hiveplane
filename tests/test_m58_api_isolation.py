"""Router-level tenant isolation and permission gates (M58-05).

Each previously-unscoped router is exercised through the HTTP API:

- tenant B reading tenant A's resource looks like it does not exist (404);
- a caller-supplied ``tenant_id`` cannot select another tenant's data;
- reads are gated on ``FLEET_READ`` when authentication is enabled.

Seeding happens through the application's own services/stores with an explicit
``TenantContext``, so the assertions exercise the request path, not the store.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
from hiveplane.api.deps import TENANT_HEADER
from hiveplane.auth.models import Scope
from hiveplane.core.approval import ApprovalRecord, ApprovalStatus
from hiveplane.cost.models import CostEvent
from hiveplane.fleet.cost import CostType
from hiveplane.learning.models import FeedbackVerdict, RunFeedback
from hiveplane.scheduler.models import QosClass, QueueEntry
from hiveplane.tenancy import Role
from hiveplane.tenancy.context import SYSTEM_CONTEXT, TenantContext
from test_tenant_scoping_isolation import _certification_record
from test_tenant_scoping_isolation import _worker as _scoped_worker

ManifestFactory = Callable[..., Any]

_TENANT_A = "tenant-a"
_TENANT_B = "tenant-b"
_NOW = datetime(2026, 9, 25, tzinfo=UTC)

_CTX_A = TenantContext(tenant_id=_TENANT_A, role=Role.ADMIN)
_CTX_B = TenantContext(tenant_id=_TENANT_B, role=Role.ADMIN)


def _as(tenant: str) -> dict[str, str]:
    return {TENANT_HEADER: tenant}


def _app() -> Any:
    return create_app()


# --------------------------------------------------------------------------- #
# Registry
# --------------------------------------------------------------------------- #
def test_registry_reads_are_tenant_scoped(make_manifest: ManifestFactory) -> None:
    app = _app()
    app.state.registry_service.create(make_manifest(name="agent-a"), ctx=_CTX_A)
    client = TestClient(app)

    assert client.get("/workloads/agent-a", headers=_as(_TENANT_A)).status_code == 200
    assert client.get("/workloads/agent-a", headers=_as(_TENANT_B)).status_code == 404
    assert client.get("/workloads", headers=_as(_TENANT_B)).json() == []
    assert client.get(
        "/workloads/agent-a/versions", headers=_as(_TENANT_B)
    ).status_code == 404


def test_registry_dry_run_ignores_foreign_workload(make_manifest: ManifestFactory) -> None:
    app = _app()
    app.state.registry_service.create(make_manifest(name="agent-a"), ctx=_CTX_A)
    client = TestClient(app)

    response = client.post(
        "/workloads",
        headers=_as(_TENANT_B),
        json=make_manifest(name="agent-a").model_dump(by_alias=True, mode="json"),
        params={"dry_run": "true"},
    )

    assert response.status_code == 200


# --------------------------------------------------------------------------- #
# Certifications
# --------------------------------------------------------------------------- #
def test_certification_reads_are_tenant_scoped() -> None:
    app = _app()
    app.state.certification_coordinator.store.add(
        _certification_record("rec-1", "agent-a"), ctx=_CTX_A
    )
    client = TestClient(app)

    assert client.get(
        "/certifications/rec-1", headers=_as(_TENANT_A)
    ).status_code == 200
    assert client.get(
        "/certifications/rec-1", headers=_as(_TENANT_B)
    ).status_code == 404
    assert client.get("/certifications", headers=_as(_TENANT_B)).json() == []


# --------------------------------------------------------------------------- #
# Approvals
# --------------------------------------------------------------------------- #
def _approval(tenant_id: str) -> ApprovalRecord:
    return ApprovalRecord(
        approval_id="ap-1",
        run_id="run-1",
        workload="agent-a",
        rule="approvals.required",
        reason="approval required",
        requested_at=_NOW,
        status=ApprovalStatus.PENDING,
        tenant_id=tenant_id,
    )


def test_approval_reads_are_tenant_scoped() -> None:
    app = _app()
    app.state.approval_service._store.save(_approval(_TENANT_A), ctx=_CTX_A)
    client = TestClient(app)

    assert client.get("/approvals", headers=_as(_TENANT_A)).json() != []
    assert client.get("/approvals", headers=_as(_TENANT_B)).json() == []
    assert client.get("/approvals/ap-1", headers=_as(_TENANT_B)).status_code == 404


# --------------------------------------------------------------------------- #
# Learning
# --------------------------------------------------------------------------- #
def _feedback(tenant_id: str) -> RunFeedback:
    return RunFeedback(
        feedback_id="fb-1",
        run_id="run-1",
        workload_id="agent-a",
        verdict=FeedbackVerdict.GOOD,
        operator="alice",
        created_at=_NOW,
        tenant_id=tenant_id,
    )


def test_feedback_reads_are_tenant_scoped() -> None:
    app = _app()
    app.state.feedback_service._store.add_feedback(_feedback(_TENANT_A), ctx=_CTX_A)
    client = TestClient(app)

    assert client.get("/feedback", headers=_as(_TENANT_A)).json() != []
    assert client.get("/feedback", headers=_as(_TENANT_B)).json() == []
    assert client.get(
        "/eval/samples", headers=_as(_TENANT_B)
    ).json() == []


# --------------------------------------------------------------------------- #
# Health
# --------------------------------------------------------------------------- #
def test_health_is_tenant_scoped(make_manifest: ManifestFactory) -> None:
    app = _app()
    app.state.registry_service.create(make_manifest(name="agent-a"), ctx=_CTX_A)
    client = TestClient(app)

    fleet_a = client.get("/health", headers=_as(_TENANT_A)).json()
    assert [entry["workload"] for entry in fleet_a] == ["agent-a"]
    assert client.get("/health", headers=_as(_TENANT_B)).json() == []
    assert client.get(
        "/health/workloads/agent-a", headers=_as(_TENANT_B)
    ).status_code == 404


# --------------------------------------------------------------------------- #
# Cost: caller-supplied tenant_id is ignored
# --------------------------------------------------------------------------- #
def _cost_event(tenant_id: str) -> CostEvent:
    return CostEvent(
        event_id="ev-1",
        tenant_id=tenant_id,
        team_id="platform",
        workload_id="agent-a",
        cost_type=CostType.LLM,
        cost_usd=5.0,
        occurred_at=_NOW,
    )


def test_cost_showback_ignores_caller_tenant() -> None:
    app = _app()
    app.state.cost_service._store.save_event(_cost_event(_TENANT_A), ctx=_CTX_A)
    client = TestClient(app)

    response = client.get(
        "/cost/showback",
        headers=_as(_TENANT_B),
        params={"tenant_id": _TENANT_A, "period": "month"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["tenant_id"] == _TENANT_B
    assert body["total_cost_usd"] == 0.0


# --------------------------------------------------------------------------- #
# Workers: caller-supplied tenant_id is ignored
# --------------------------------------------------------------------------- #
def test_workers_ignore_caller_tenant() -> None:
    app = _app()
    app.state.worker_registry._store.save_worker(
        _scoped_worker(_TENANT_A), ctx=_CTX_A
    )
    client = TestClient(app)

    as_a = client.get("/workers", headers=_as(_TENANT_A)).json()
    assert [entry["worker"]["worker_id"] for entry in as_a] == ["w1"]

    response = client.get(
        "/workers", headers=_as(_TENANT_B), params={"tenant_id": _TENANT_A}
    )
    assert response.status_code == 200
    assert response.json() == []


# --------------------------------------------------------------------------- #
# Scheduler: snapshot is tenant-scoped; caller tenant_id ignored
# --------------------------------------------------------------------------- #
def test_queue_snapshot_is_tenant_scoped() -> None:
    app = _app()
    app.state.scheduler.submit(
        QueueEntry(
            task_id="task-a",
            tenant_id=_TENANT_A,
            workload="agent-a",
            qos=QosClass.BEST_EFFORT,
            priority=0,
            submitted_at=_NOW,
        )
    )
    client = TestClient(app)

    assert client.get("/queue", headers=_as(_TENANT_A)).json()["depth"] == 1
    response = client.get(
        "/queue", headers=_as(_TENANT_B), params={"tenant_id": _TENANT_A}
    )
    assert response.status_code == 200
    assert response.json()["depth"] == 0


# --------------------------------------------------------------------------- #
# Progressive
# --------------------------------------------------------------------------- #
def test_canary_list_is_tenant_scoped(make_manifest: ManifestFactory) -> None:
    app = _app()
    app.state.registry_service.create(make_manifest(name="agent-a"), ctx=_CTX_A)
    app.state.canary_service.start(
        "agent-a",
        candidate_version=2,
        traffic_pct=10,
        window_seconds=60,
        min_sample=1,
        ctx=_CTX_A,
    )
    client = TestClient(app)

    assert client.get("/canary", headers=_as(_TENANT_A)).json() != []
    assert client.get("/canary", headers=_as(_TENANT_B)).json() == []


# --------------------------------------------------------------------------- #
# Permission gates
# --------------------------------------------------------------------------- #
def test_ungated_read_requires_fleet_read(
    monkeypatch: pytest.MonkeyPatch, make_manifest: ManifestFactory
) -> None:
    monkeypatch.setenv("HIVEPLANE_AUTH__ENABLED", "true")
    from hiveplane.config import get_settings

    get_settings.cache_clear()
    app = create_app()
    app.state.registry_service.create(make_manifest(name="agent-a"), ctx=_CTX_A)
    key = app.state.auth_service.keys.create(
        _TENANT_A, Role.ADMIN, scopes=[Scope.APPROVALS_WRITE], ctx=SYSTEM_CONTEXT
    )
    client = TestClient(app)

    response = client.get(
        "/workloads", headers={"Authorization": f"Bearer {key.token}"}
    )

    assert response.status_code == 403
