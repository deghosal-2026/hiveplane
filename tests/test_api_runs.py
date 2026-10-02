"""Tests for run-intervention authorization and attribution."""

from __future__ import annotations

from collections.abc import Callable

import pytest
from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
from hiveplane.config import get_settings
from hiveplane.core.run import AdmissionContext, RunState
from hiveplane.core.workload import AgentWorkload
from hiveplane.tenancy.models import Role


def _running_run(app: object) -> object:
    runs = app.state.run_service  # type: ignore[attr-defined]
    run = runs.submit(workload="agent-1", caller="cli", context=AdmissionContext.SANDBOX)
    runs.transition(run.id, RunState.RUNNING, actor="scheduler")
    return run


def test_run_intervention_records_principal(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    app = create_app()
    app.state.registry_service.create(
        make_manifest(name="agent-1")
    )
    run = _running_run(app)
    client = TestClient(app)

    response = client.post(f"/runs/{run.id}/stop")  # type: ignore[attr-defined]

    assert response.status_code == 200
    events = app.state.run_service.events(run.id)  # type: ignore[attr-defined]
    assert any(event.actor == "anonymous" for event in events)
    assert all(event.actor != "api" for event in events)


def test_run_deliveries_lists_fan_out_attempts(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    app = create_app()
    app.state.registry_service.create(
        make_manifest(
            name="agent-1",
            fan_out={"on_completed": [{"type": "slack", "channel": "#support"}]},
        )
    )
    runs = app.state.run_service
    run = runs.submit(workload="agent-1", caller="cli", context=AdmissionContext.SANDBOX)
    runs.transition(run.id, RunState.RUNNING, actor="scheduler")
    runs.transition(run.id, RunState.COMPLETED, actor="runner")
    client = TestClient(app)

    response = client.get(f"/runs/{run.id}/deliveries")

    assert response.status_code == 200
    deliveries = response.json()
    assert deliveries, "a completed run must expose its fan-out delivery attempts"
    assert deliveries[0]["run_id"] == run.id


def test_viewer_cannot_stop_run(
    monkeypatch: pytest.MonkeyPatch, make_manifest: Callable[..., AgentWorkload]
) -> None:
    monkeypatch.setenv("HIVEPLANE_AUTH__ENABLED", "true")
    get_settings.cache_clear()
    app = create_app()
    app.state.registry_service.create(
        make_manifest(name="agent-1")
    )
    run = _running_run(app)
    viewer = app.state.auth_service.keys.create("default", Role.VIEWER)
    client = TestClient(app)

    response = client.post(
        f"/runs/{run.id}/stop", headers={"Authorization": f"Bearer {viewer.token}"}  # type: ignore[attr-defined]
    )

    assert response.status_code == 403
    assert app.state.run_service.get(run.id).state is RunState.RUNNING  # type: ignore[attr-defined]
