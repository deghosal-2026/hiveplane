"""Tests for the policy and approval API."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import FastAPI
from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
from hiveplane.core.run import AdmissionContext, RunState
from hiveplane.core.workload import AgentWorkload
from hiveplane.registry.service import RegistryService


def _app(make_manifest: Callable[..., AgentWorkload]) -> FastAPI:
    app = create_app()
    registry: RegistryService = app.state.registry_service
    registry.create(
        make_manifest(
            name="agent-1",
            fan_out={"on_escalation": [{"type": "slack", "channel": "#ops"}]},
        )
    )
    return app


def _setup(make_manifest: Callable[..., AgentWorkload]) -> TestClient:
    return TestClient(_app(make_manifest))


def test_evaluate_denies_unlisted_tool(make_manifest: Callable[..., AgentWorkload]) -> None:
    client = _setup(make_manifest)
    response = client.post(
        "/policy/evaluate",
        json={
            "run_id": "run-1",
            "workload": "agent-1",
            "environment": "staging",
            "tool_id": "not-allowed",
        },
    )
    assert response.status_code == 200
    assert response.json()["outcome"] == "deny"
    assert response.json()["rule"] == "default.deny"


def test_register_and_list_policy_packs(make_manifest: Callable[..., AgentWorkload]) -> None:
    client = _setup(make_manifest)
    pack = {
        "apiVersion": "hiveplane/v1",
        "kind": "PolicyPack",
        "metadata": {"name": "platform-default", "team": "platform", "version": "1.0.0"},
        "spec": {"defaults": {}, "overrides": []},
    }
    assert client.post("/policy-packs", json=pack).status_code == 201
    assert client.get("/policy-packs").json()[0]["metadata"]["name"] == "platform-default"
    assert client.post("/policy-packs", json=pack).status_code == 409


def test_approve_resumes_and_deny_fails(make_manifest: Callable[..., AgentWorkload]) -> None:
    app = _app(make_manifest)
    client = TestClient(app)
    runs = app.state.run_service
    approvals = app.state.approval_service

    run = runs.submit(workload="agent-1", caller="cli", context=AdmissionContext.SANDBOX)
    runs.transition(run.id, RunState.RUNNING, actor="scheduler")
    runs.transition(run.id, RunState.PAUSED, actor="operator")
    request = approvals.request(
        run_id=run.id, workload="agent-1", rule="approvals.required", reason="test"
    )
    approved = client.post(f"/approvals/{request.approval_id}/approve", json={"operator": "alice"})
    assert approved.status_code == 200
    assert runs.get(run.id).state is RunState.RUNNING

    run2 = runs.submit(workload="agent-1", caller="cli", context=AdmissionContext.SANDBOX)
    runs.transition(run2.id, RunState.RUNNING, actor="scheduler")
    runs.transition(run2.id, RunState.PAUSED, actor="operator")
    request2 = approvals.request(run_id=run2.id, workload="agent-1", rule="r", reason="test")
    denied = client.post(
        f"/approvals/{request2.approval_id}/deny", json={"operator": "bob", "reason": "no"}
    )
    assert denied.status_code == 200
    assert runs.get(run2.id).state is RunState.FAILED


def test_unknown_approval_returns_404(make_manifest: Callable[..., AgentWorkload]) -> None:
    client = _setup(make_manifest)
    assert client.get("/approvals/nope").status_code == 404
