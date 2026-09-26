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


def test_policy_dry_run_matches_the_real_decision(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    client = _setup(make_manifest)
    body = {
        "run_id": "run-1",
        "workload": "agent-1",
        "environment": "production",
        "tool_id": "mcp.t.read",
        "action_class": "read_only",
    }

    real = client.post("/policy/evaluate", json=body).json()
    what_if = client.post("/policy/evaluate", json={**body, "dry_run": True}).json()

    assert what_if["outcome"] == real["outcome"]
    assert what_if["rule"] == real["rule"]
    assert what_if["dry_run"] is True
    assert real["dry_run"] is False


def test_kill_switch_api_disables_and_enables() -> None:
    client = TestClient(create_app())

    disabled = client.post(
        "/tools/mcp.t.read/disable", json={"actor": "alice", "reason": "incident"}
    )
    assert disabled.status_code == 200
    assert disabled.json()["disabled"] is True
    assert client.get("/tools/disabled").json()[0]["tool_id"] == "mcp.t.read"

    enabled = client.post("/tools/mcp.t.read/enable", json={"actor": "alice"})
    assert enabled.status_code == 200
    assert enabled.json()["disabled"] is False


def test_policy_pack_publish_and_apply() -> None:
    client = TestClient(create_app())
    pack = {
        "apiVersion": "hiveplane/v1",
        "kind": "PolicyPack",
        "metadata": {"name": "strict", "team": "platform", "version": "1"},
        "spec": {"overrides": []},
    }
    assert client.post("/policy-packs", json=pack).status_code == 201

    applied = client.post("/policy-packs/strict/apply", json={"team": "payments"})

    assert applied.status_code == 200
    assert applied.json()[0]["metadata"]["team"] == "payments"


def test_policy_pack_apply_unknown_is_404() -> None:
    client = TestClient(create_app())

    assert client.post("/policy-packs/ghost/apply", json={"team": "payments"}).status_code == 404
