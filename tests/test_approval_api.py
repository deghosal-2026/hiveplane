"""Tests for the approval API's transactional behavior."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
from hiveplane.core.approval import ApprovalStatus
from hiveplane.core.run import AdmissionContext, RunState
from hiveplane.core.workload import AgentWorkload
from hiveplane.registry.service import RegistryService


def _app(make_manifest: Callable[..., AgentWorkload]) -> tuple[TestClient, object]:
    app = create_app()
    registry: RegistryService = app.state.registry_service
    registry.create(make_manifest(name="agent-1"))
    return TestClient(app), app


def _approval_for(app: object, run_id: str) -> object:
    return app.state.approval_service.request(  # type: ignore[attr-defined]
        run_id=run_id, workload="agent-1", rule="approvals.required", reason="needs review"
    )


def test_comment_and_delegate_persist(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    client, app = _app(make_manifest)
    approval = _approval_for(app, "run-1")
    approval_id = approval.approval_id  # type: ignore[attr-defined]

    commented = client.post(
        f"/approvals/{approval_id}/comments",
        json={"author": "alice", "text": "looking into it"},
    )
    assert commented.status_code == 200
    assert commented.json()["comments"][0]["text"] == "looking into it"

    delegated = client.post(
        f"/approvals/{approval_id}/delegate",
        json={"assignee": "bob", "operator": "alice"},
    )
    assert delegated.status_code == 200
    body = delegated.json()
    assert body["delegated_to"] == "bob"
    assert body["delegated_by"] == "anonymous"

    stored = app.state.approval_service.get(approval_id)  # type: ignore[attr-defined]
    assert stored.delegated_to == "bob"
    assert [comment.text for comment in stored.comments] == [
        "looking into it",
        "delegated to bob",
    ]


def test_comment_and_delegate_are_tenant_scoped(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    client, app = _app(make_manifest)
    approval = _approval_for(app, "run-1")
    approval_id = approval.approval_id  # type: ignore[attr-defined]
    headers = {"X-Hiveplane-Tenant": "other"}

    commented = client.post(
        f"/approvals/{approval_id}/comments",
        json={"author": "mallory", "text": "tamper"},
        headers=headers,
    )
    delegated = client.post(
        f"/approvals/{approval_id}/delegate",
        json={"assignee": "mallory", "operator": "mallory"},
        headers=headers,
    )

    assert commented.status_code == 404
    assert delegated.status_code == 404
    stored = app.state.approval_service.get(approval_id)  # type: ignore[attr-defined]
    assert stored.comments == []
    assert stored.delegated_to is None


def test_viewer_cannot_comment_or_delegate(
    monkeypatch: object, make_manifest: Callable[..., AgentWorkload]
) -> None:
    monkeypatch.setenv("HIVEPLANE_AUTH__ENABLED", "true")  # type: ignore[attr-defined]
    from hiveplane.config import get_settings

    get_settings.cache_clear()
    from hiveplane.tenancy.models import Role

    app = create_app()
    registry: RegistryService = app.state.registry_service
    registry.create(make_manifest(name="agent-1"))
    viewer = app.state.auth_service.keys.create("default", Role.VIEWER)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {viewer.token}"}

    assert (
        client.post(
            "/approvals/missing/comments",
            json={"author": "alice", "text": "hi"},
            headers=headers,
        ).status_code
        == 403
    )
    assert (
        client.post(
            "/approvals/missing/delegate",
            json={"assignee": "bob", "operator": "alice"},
            headers=headers,
        ).status_code
        == 403
    )


def test_approve_stopped_run_is_rejected(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    client, app = _app(make_manifest)
    runs = app.state.run_service  # type: ignore[attr-defined]
    run = runs.submit(workload="agent-1", caller="cli", context=AdmissionContext.SANDBOX)
    runs.transition(run.id, RunState.RUNNING, actor="scheduler")
    runs.transition(run.id, RunState.CANCELLED, actor="operator")
    approval = _approval_for(app, run.id)

    response = client.post(
        f"/approvals/{approval.approval_id}/approve", json={"operator": "op"}  # type: ignore[attr-defined]
    )

    assert response.status_code == 409
    assert (
        app.state.approval_service.get(approval.approval_id).status  # type: ignore[attr-defined]
        is ApprovalStatus.PENDING
    )


def test_approve_paused_run_resumes(make_manifest: Callable[..., AgentWorkload]) -> None:
    client, app = _app(make_manifest)
    runs = app.state.run_service  # type: ignore[attr-defined]
    run = runs.submit(workload="agent-1", caller="cli", context=AdmissionContext.SANDBOX)
    runs.transition(run.id, RunState.RUNNING, actor="scheduler")
    runs.transition(run.id, RunState.PAUSED, actor="policy")
    approval = _approval_for(app, run.id)

    response = client.post(
        f"/approvals/{approval.approval_id}/approve", json={"operator": "op"}  # type: ignore[attr-defined]
    )

    assert response.status_code == 200
    assert runs.get(run.id).state is RunState.RUNNING


def test_approval_actor_is_authenticated_principal(
    monkeypatch: pytest.MonkeyPatch, make_manifest: Callable[..., AgentWorkload]
) -> None:
    monkeypatch.setenv("HIVEPLANE_AUTH__ENABLED", "true")
    from hiveplane.config import get_settings

    get_settings.cache_clear()
    from hiveplane.tenancy.models import Role

    app = create_app()
    registry: RegistryService = app.state.registry_service
    registry.create(make_manifest(name="agent-1"))
    runs = app.state.run_service
    run = runs.submit(workload="agent-1", caller="cli", context=AdmissionContext.SANDBOX)
    runs.transition(run.id, RunState.RUNNING, actor="scheduler")
    runs.transition(run.id, RunState.PAUSED, actor="policy")
    approval = _approval_for(app, run.id)
    approver = app.state.auth_service.keys.create("default", Role.APPROVER)
    client = TestClient(app)
    headers = {"Authorization": f"Bearer {approver.token}"}

    response = client.post(
        f"/approvals/{approval.approval_id}/approve",  # type: ignore[attr-defined]
        json={"operator": "bob", "reason": "ok"},
        headers=headers,
    )

    assert response.status_code == 200
    stored = app.state.approval_service.get(approval.approval_id)  # type: ignore[attr-defined]
    assert stored.decided_by == approver.record.key_id
    assert stored.decided_by != "bob"
