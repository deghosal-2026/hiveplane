"""Tests for the approval API's transactional behavior."""

from __future__ import annotations

from collections.abc import Callable

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
