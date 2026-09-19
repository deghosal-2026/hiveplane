"""Tests for the operator UI application shell (M22, #83, #86, #87)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from hiveplane.ui.app import create_ui_app
from hiveplane.ui.client import ControlPlaneError
from ui_fakes import FakeControlPlaneClient


def _app(fake: FakeControlPlaneClient) -> TestClient:
    return TestClient(create_ui_app(client=fake), follow_redirects=False)


def _fleet_fake() -> FakeControlPlaneClient:
    fake = FakeControlPlaneClient()
    fake.workloads = [
        {
            "name": "agent-a",
            "owner": "alice",
            "team": "platform",
            "runtime": "raw-worker",
            "certification_status": "certified",
            "current_version": 1,
            "updated_at": "2026-09-19T10:00:00Z",
            "last_run_at": "2026-09-19T11:00:00Z",
        }
    ]
    fake.runs = [
        {"id": "r1", "workload_id": "agent-a", "state": "failed"},
        {"id": "r2", "workload_id": "agent-a", "state": "completed"},
    ]
    fake.spend = {
        "by_workload": [
            {"workload": "agent-a", "team": "platform", "total_usd": 1.5, "run_count": 2}
        ],
        "by_team": [{"team": "platform", "total_usd": 1.5, "run_count": 2}],
    }
    return fake


def _story_fake() -> FakeControlPlaneClient:
    fake = FakeControlPlaneClient()
    fake.story = {
        "run_id": "r1",
        "workload": "agent-a",
        "team": "platform",
        "state": "running",
        "context": "production",
        "model_identity": "openai:gpt-4o",
        "cost_usd": 1.25,
        "sandbox": True,
        "sandbox_id": "sbx-1",
        "certification_status": "certified",
        "attestation_id": "att-1",
        "trace_id": "trace-1",
        "entries": [
            {
                "timestamp": "2026-09-19T11:00:00Z",
                "kind": "admission",
                "summary": "admitted",
                "detail": {},
            }
        ],
    }
    return fake


# --------------------------------------------------------------------------- #
# Shell (#83)
# --------------------------------------------------------------------------- #
def test_healthz_reports_ok() -> None:
    client = TestClient(create_ui_app(client=FakeControlPlaneClient()))

    assert client.get("/healthz").json() == {"status": "ok"}


def test_default_client_uses_configured_api_url() -> None:
    app = create_ui_app()

    assert app.state.control_plane.base_url == "http://localhost:8000"


def test_control_plane_error_renders_502_page() -> None:
    fake = FakeControlPlaneClient()
    fake.errors["list_workloads"] = ControlPlaneError(503, "control plane down")
    app = create_ui_app(client=fake)

    @app.get("/boom")
    def boom() -> dict[str, Any]:
        app.state.control_plane.list_workloads()
        return {}

    response = TestClient(app, raise_server_exceptions=False).get("/boom")

    assert response.status_code == 502
    assert "control plane down" in response.text


# --------------------------------------------------------------------------- #
# Fleet (#86, #54)
# --------------------------------------------------------------------------- #
def test_fleet_renders_workloads_and_totals() -> None:
    response = _app(_fleet_fake()).get("/")

    assert response.status_code == 200
    assert "agent-a" in response.text
    assert "certified" in response.text
    assert "1.50" in response.text or "1.5" in response.text
    assert 'href="/runs/r1"' in response.text


def test_fleet_renders_empty_state() -> None:
    response = _app(FakeControlPlaneClient()).get("/")

    assert response.status_code == 200
    assert "No workloads" in response.text


# --------------------------------------------------------------------------- #
# Run detail (#86, #54)
# --------------------------------------------------------------------------- #
def test_run_detail_renders_story() -> None:
    response = _app(_story_fake()).get("/runs/r1")

    assert response.status_code == 200
    assert "agent-a" in response.text
    assert "admitted" in response.text
    assert "trace-1" in response.text


def test_run_detail_unknown_run_renders_404() -> None:
    fake = _story_fake()
    fake.errors["get_story"] = ControlPlaneError(404, "run 'ghost' not found")

    response = _app(fake).get("/runs/ghost")

    assert response.status_code == 404
    assert "No run" in response.text


def test_run_detail_control_plane_outage_renders_502() -> None:
    fake = _story_fake()
    fake.errors["get_story"] = ControlPlaneError(503, "control plane down")

    response = TestClient(create_ui_app(client=fake), raise_server_exceptions=False).get(
        "/runs/r1"
    )

    assert response.status_code == 502
    assert "control plane down" in response.text


@pytest.mark.parametrize("action", ["pause", "resume", "stop"])
def test_run_intervention_calls_client_and_redirects(action: str) -> None:
    fake = _story_fake()

    response = _app(fake).post(f"/runs/r1/{action}")

    assert response.status_code == 303
    assert response.headers["location"].startswith("/runs/r1")
    assert (action, ("r1",)) in fake.calls


def test_failed_intervention_redirects_with_error_banner() -> None:
    fake = _story_fake()
    fake.errors["pause"] = ControlPlaneError(409, "run is not paused")

    client = TestClient(create_ui_app(client=fake), follow_redirects=True)
    response = client.post("/runs/r1/pause")

    assert response.status_code == 200
    assert "run is not paused" in response.text


# --------------------------------------------------------------------------- #
# Approval queue (#87, #55)
# --------------------------------------------------------------------------- #
def _approvals_fake() -> FakeControlPlaneClient:
    fake = FakeControlPlaneClient()
    fake.approvals = [
        {
            "approval_id": "a1",
            "run_id": "r1",
            "workload": "agent-a",
            "rule": "destructive-tool",
            "reason": "delete requested",
            "action_class": "destructive",
            "requested_at": "2026-09-19T11:00:00Z",
            "status": "pending",
        },
        {
            "approval_id": "a2",
            "run_id": "r2",
            "workload": "agent-b",
            "rule": "budget",
            "reason": "over budget",
            "action_class": "spend",
            "requested_at": "2026-09-19T10:00:00Z",
            "status": "approved",
            "decided_by": "alice",
            "decision_reason": "lgtm",
        },
    ]
    return fake


def test_approvals_renders_pending_and_resolved() -> None:
    response = _app(_approvals_fake()).get("/approvals")

    assert response.status_code == 200
    assert "a1" in response.text
    assert "destructive-tool" in response.text
    assert "a2" in response.text
    assert "alice" in response.text
    assert 'href="/runs/r1"' in response.text


def test_approve_posts_operator_and_reason() -> None:
    fake = _approvals_fake()

    response = _app(fake).post(
        "/approvals/a1/approve", data={"operator": "alice", "reason": "lgtm"}
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/approvals")
    assert ("approve", ("a1", "alice", "lgtm")) in fake.calls


def test_deny_posts_operator_and_reason() -> None:
    fake = _approvals_fake()

    response = _app(fake).post(
        "/approvals/a1/deny", data={"operator": "bob", "reason": "too risky"}
    )

    assert response.status_code == 303
    assert ("deny", ("a1", "bob", "too risky")) in fake.calls


def test_failed_decision_redirects_with_error_banner() -> None:
    fake = _approvals_fake()
    fake.errors["approve"] = ControlPlaneError(409, "approval already decided")

    client = TestClient(create_ui_app(client=fake), follow_redirects=True)
    response = client.post("/approvals/a1/approve", data={"operator": "alice"})

    assert response.status_code == 200
    assert "approval already decided" in response.text


# --------------------------------------------------------------------------- #
# Certification dashboard (#87, #55)
# --------------------------------------------------------------------------- #
def _certs_fake() -> FakeControlPlaneClient:
    fake = FakeControlPlaneClient()
    fake.certifications = [
        {
            "record_id": "c1",
            "certification": {
                "certification_id": "c1",
                "workload_id": "agent-a",
                "status": "certified",
                "target_context": "staging",
                "timestamp": "2026-09-19T10:00:00Z",
                "attestation_id": "att-1",
                "eval_summary": {"pass_rate": 0.9},
            },
            "attestation": {"attestation_id": "att-1"},
        },
        {
            "record_id": "c2",
            "certification": {
                "certification_id": "c2",
                "workload_id": "agent-a",
                "status": "quarantined",
                "target_context": "staging",
                "timestamp": "2026-09-19T11:00:00Z",
                "attestation_id": "att-2",
                "eval_summary": {"pass_rate": 0.5},
            },
            "attestation": {"attestation_id": "att-2"},
        },
    ]
    return fake


def test_certification_dashboard_renders_counts_trends_quarantine() -> None:
    response = _app(_certs_fake()).get("/certifications")

    assert response.status_code == 200
    assert "agent-a" in response.text
    assert "quarantined" in response.text
    assert "att-2" in response.text


def test_certification_dashboard_empty_state() -> None:
    response = _app(FakeControlPlaneClient()).get("/certifications")

    assert response.status_code == 200
    assert "No certifications" in response.text


# --------------------------------------------------------------------------- #
# Spend (#87, #55)
# --------------------------------------------------------------------------- #
def test_spend_renders_workload_and_team_tables() -> None:
    fake = FakeControlPlaneClient()
    fake.spend = {
        "by_workload": [
            {"workload": "agent-a", "team": "platform", "total_usd": 2.5, "run_count": 3}
        ],
        "by_team": [{"team": "platform", "total_usd": 2.5, "run_count": 3}],
    }

    response = _app(fake).get("/spend")

    assert response.status_code == 200
    assert "agent-a" in response.text
    assert "platform" in response.text
    assert "2.50" in response.text


def test_spend_empty_state() -> None:
    response = _app(FakeControlPlaneClient()).get("/spend")

    assert response.status_code == 200
    assert "No spend" in response.text
