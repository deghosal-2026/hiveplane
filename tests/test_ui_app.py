"""Tests for the operator UI application shell (M22, #83, #86, #87)."""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient

from hiveplane.config import get_settings
from hiveplane.tenancy.models import Role
from hiveplane.ui.app import create_ui_app
from hiveplane.ui.client import ControlPlaneError
from hiveplane.ui.session import SESSION_COOKIE, verify_session
from ui_fakes import FakeControlPlaneClient, session_token


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

    assert app.state.control_plane.base_url == "http://localhost:8100"


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


def test_error_page_keeps_identity_in_nav() -> None:
    fake = _fleet_fake()
    fake.errors["list_workloads"] = ControlPlaneError(503, "control plane down")
    client = TestClient(create_ui_app(client=fake), follow_redirects=False)
    client.cookies.set(SESSION_COOKIE, session_token(Role.APPROVER))

    response = client.get("/")

    assert response.status_code == 502
    assert 'href="/approvals"' in response.text


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


def test_run_detail_renders_model_call_content() -> None:
    fake = _story_fake()
    fake.story["entries"] = [
        {
            "timestamp": "2026-09-19T11:00:01Z",
            "kind": "model_call",
            "summary": "15 tokens, $0.0100",
            "detail": {
                "model_identity": "openai:gpt-4o",
                "prompt": "classify this PR",
                "response": "risky",
                "latency_ms": 42,
            },
        }
    ]

    response = _app(fake).get("/runs/r1")

    assert response.status_code == 200
    assert "classify this PR" in response.text
    assert "risky" in response.text


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


def test_viewer_ui_hides_intervention_controls() -> None:
    fake = _story_fake()
    client = TestClient(create_ui_app(client=fake), follow_redirects=False)
    client.cookies.set(SESSION_COOKIE, session_token(Role.VIEWER))

    response = client.get("/runs/r1")

    assert response.status_code == 200
    assert "Intervene" not in response.text


def test_viewer_cannot_stop_run_in_ui() -> None:
    fake = _story_fake()
    client = TestClient(create_ui_app(client=fake), follow_redirects=False)
    client.cookies.set(SESSION_COOKIE, session_token(Role.VIEWER))

    response = client.post("/runs/r1/stop")

    assert response.status_code == 403
    assert not any(name == "stop" for name, _ in fake.calls)


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
    assert ("approve", ("a1", "anonymous", "lgtm")) in fake.calls


def test_deny_posts_operator_and_reason() -> None:
    fake = _approvals_fake()

    response = _app(fake).post(
        "/approvals/a1/deny", data={"operator": "bob", "reason": "too risky"}
    )

    assert response.status_code == 303
    assert ("deny", ("a1", "anonymous", "too risky")) in fake.calls


def test_failed_decision_redirects_with_error_banner() -> None:
    fake = _approvals_fake()
    fake.errors["approve"] = ControlPlaneError(409, "approval already decided")

    client = TestClient(create_ui_app(client=fake), follow_redirects=True)
    response = client.post("/approvals/a1/approve", data={"operator": "alice"})

    assert response.status_code == 200
    assert "approval already decided" in response.text


# --------------------------------------------------------------------------- #
# Approval queue v2: bulk / comment / delegate (M52, #399)
# --------------------------------------------------------------------------- #
def test_bulk_approve_calls_client_per_id() -> None:
    fake = _approvals_fake()
    client = TestClient(create_ui_app(client=fake), follow_redirects=False)

    response = client.post(
        "/approvals/bulk",
        data={"decision": "approve", "approval_id": ["a1", "a2"], "operator": "alice"},
    )

    assert response.status_code == 303
    assert ("approve", ("a1", "anonymous", None)) in fake.calls
    assert ("approve", ("a2", "anonymous", None)) in fake.calls


def test_bulk_deny_redirects_with_ok_summary() -> None:
    fake = _approvals_fake()

    response = _app(fake).post(
        "/approvals/bulk",
        data={"decision": "deny", "approval_id": "a1", "operator": "bob"},
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/approvals?ok=")
    assert ("deny", ("a1", "anonymous", None)) in fake.calls


def test_bulk_empty_selection_flashes_friendly_error() -> None:
    fake = _approvals_fake()
    client = TestClient(create_ui_app(client=fake), follow_redirects=True)

    response = client.post(
        "/approvals/bulk", data={"decision": "approve", "operator": "alice"}
    )

    assert response.status_code == 200
    assert "no approvals selected" in response.text
    assert not any(name in {"approve", "deny"} for name, _ in fake.calls)


def test_bulk_rejects_unknown_decision() -> None:
    fake = _approvals_fake()

    response = _app(fake).post(
        "/approvals/bulk",
        data={"decision": "maybe", "approval_id": "a1", "operator": "alice"},
    )

    assert response.status_code == 422
    assert not any(name in {"approve", "deny"} for name, _ in fake.calls)


def test_bulk_reports_per_id_failure_in_flash() -> None:
    fake = _approvals_fake()
    fake.errors["approve"] = ControlPlaneError(409, "already decided")

    client = TestClient(create_ui_app(client=fake), follow_redirects=True)
    response = client.post(
        "/approvals/bulk",
        data={"decision": "approve", "approval_id": ["a1", "a2"], "operator": "alice"},
    )

    assert response.status_code == 200
    assert "a1" in response.text
    assert "a2" in response.text
    assert "already decided" in response.text


def test_viewer_is_denied_bulk() -> None:
    fake = _approvals_fake()
    client = TestClient(create_ui_app(client=fake), follow_redirects=False)
    client.cookies.set(SESSION_COOKIE, session_token(Role.VIEWER))

    response = client.post(
        "/approvals/bulk",
        data={"decision": "approve", "approval_id": "a1", "operator": "v"},
    )

    assert response.status_code == 403
    assert not any(name in {"approve", "deny"} for name, _ in fake.calls)


def test_comment_posts_author_and_text() -> None:
    fake = _approvals_fake()

    response = _app(fake).post(
        "/approvals/a1/comment", data={"author": "alice", "text": "looks good"}
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/approvals")
    assert ("add_approval_comment", ("a1", "anonymous", "looks good")) in fake.calls


def test_delegate_posts_assignee_and_operator() -> None:
    fake = _approvals_fake()

    response = _app(fake).post(
        "/approvals/a1/delegate", data={"assignee": "bob", "operator": "alice"}
    )

    assert response.status_code == 303
    assert ("delegate_approval", ("a1", "bob", "anonymous")) in fake.calls


@pytest.mark.parametrize("path", ["/approvals/a1/comment", "/approvals/a1/delegate"])
def test_viewer_is_denied_comment_and_delegate(path: str) -> None:
    fake = _approvals_fake()
    client = TestClient(create_ui_app(client=fake), follow_redirects=False)
    client.cookies.set(SESSION_COOKIE, session_token(Role.VIEWER))

    response = client.post(
        path,
        data={"author": "v", "text": "x", "assignee": "v", "operator": "v"},
    )

    assert response.status_code == 403
    assert not any(
        name in {"add_approval_comment", "delegate_approval"} for name, _ in fake.calls
    )


def test_failed_comment_redirects_with_error_banner() -> None:
    fake = _approvals_fake()
    fake.errors["add_approval_comment"] = ControlPlaneError(404, "approval not found")

    client = TestClient(create_ui_app(client=fake), follow_redirects=True)
    response = client.post("/approvals/a1/comment", data={"author": "alice", "text": "x"})

    assert response.status_code == 200
    assert "approval not found" in response.text


def test_failed_delegate_redirects_with_error_banner() -> None:
    fake = _approvals_fake()
    fake.errors["delegate_approval"] = ControlPlaneError(404, "assignee unknown")

    client = TestClient(create_ui_app(client=fake), follow_redirects=True)
    response = client.post(
        "/approvals/a1/delegate", data={"assignee": "bob", "operator": "alice"}
    )

    assert response.status_code == 200
    assert "assignee unknown" in response.text


def test_approvals_renders_bulk_comments_and_delegation() -> None:
    fake = _approvals_fake()
    fake.approvals[0]["delegated_to"] = "bob"
    fake.approvals[0]["comments"] = [
        {
            "author": "carol",
            "text": "please rotate credentials",
            "created_at": "2026-09-19T12:00:00Z",
        }
    ]

    response = _app(fake).get("/approvals")

    assert response.status_code == 200
    assert "bob" in response.text
    assert "please rotate credentials" in response.text
    assert 'action="/approvals/bulk"' in response.text
    assert 'action="/approvals/a1/comment"' in response.text
    assert 'action="/approvals/a1/delegate"' in response.text


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


# --------------------------------------------------------------------------- #
# Cost / ROI / health dashboards (M52, #400)
# --------------------------------------------------------------------------- #
def _cost_fake() -> FakeControlPlaneClient:
    fake = FakeControlPlaneClient()
    fake.spend = {
        "by_workload": [
            {"workload": "agent-a", "team": "platform", "total_usd": 4.0, "run_count": 2},
            {"workload": "agent-b", "team": "ops", "total_usd": 1.0, "run_count": 1},
        ],
        "by_team": [{"team": "platform", "total_usd": 4.0, "run_count": 2}],
    }
    return fake


def test_cost_renders_spend_chart_and_table() -> None:
    response = _app(_cost_fake()).get("/cost")

    assert response.status_code == 200
    assert "agent-a" in response.text
    assert "agent-b" in response.text
    assert "<svg" in response.text
    assert 'width="100%' in response.text


def test_cost_empty_state() -> None:
    response = _app(FakeControlPlaneClient()).get("/cost")

    assert response.status_code == 200
    assert "No spend" in response.text


def _roi_fake() -> FakeControlPlaneClient:
    fake = FakeControlPlaneClient()
    fake.roi = {
        "fleet_roi": 1.25,
        "total_spend_usd": 55.0,
        "rows": [
            {
                "workload_id": "spendy",
                "spend_usd": 50.0,
                "value_usd": 5.0,
                "roi": 0.1,
                "expensive_low_value": True,
                "evidence": ["high spend for low value"],
            },
            {
                "workload_id": "efficient",
                "spend_usd": 5.0,
                "value_usd": 20.0,
                "roi": 4.0,
                "expensive_low_value": False,
                "evidence": [],
            },
        ],
    }
    return fake


def test_roi_renders_flagged_rows_and_evidence() -> None:
    response = _app(_roi_fake()).get("/roi")

    assert response.status_code == 200
    assert "spendy" in response.text
    assert "efficient" in response.text
    assert "high spend for low value" in response.text


def test_roi_empty_state() -> None:
    response = _app(FakeControlPlaneClient()).get("/roi")

    assert response.status_code == 200
    assert "No ROI" in response.text


def _health_fake() -> FakeControlPlaneClient:
    fake = FakeControlPlaneClient()
    fake.health = [
        {
            "workload": "agent-a",
            "failure_rate": 0.01,
            "status": "healthy",
            "objectives": [
                {"objective": "availability", "remaining": 0.9},
            ],
        },
        {
            "workload": "agent-b",
            "failure_rate": 0.2,
            "status": "degraded",
            "objectives": [],
        },
    ]
    return fake


def test_health_renders_status_success_rate_and_budget() -> None:
    response = _app(_health_fake()).get("/health")

    assert response.status_code == 200
    assert "agent-a" in response.text
    assert "agent-b" in response.text
    assert "99.0%" in response.text
    assert "degraded" in response.text


def test_health_empty_state() -> None:
    response = _app(FakeControlPlaneClient()).get("/health")

    assert response.status_code == 200
    assert "No health" in response.text


def test_feedback_posts_to_client_and_redirects() -> None:
    fake = _story_fake()

    response = _app(fake).post(
        "/runs/r1/feedback",
        data={"verdict": "failed-with-lesson", "notes": "escalate", "operator": "alice"},
    )

    assert response.status_code == 303
    assert response.headers["location"].startswith("/runs/r1")
    assert (
        "record_feedback",
        ("r1", "failed-with-lesson", "escalate", "anonymous"),
    ) in fake.calls


def test_failed_feedback_redirects_with_error_banner() -> None:
    fake = _story_fake()
    fake.errors["record_feedback"] = ControlPlaneError(409, "run is not terminal")

    client = TestClient(create_ui_app(client=fake), follow_redirects=True)
    response = client.post(
        "/runs/r1/feedback", data={"verdict": "good", "notes": "", "operator": "alice"}
    )

    assert response.status_code == 200
    assert "run is not terminal" in response.text


# --------------------------------------------------------------------------- #
# Trigger log and queue visualizer (M52, #401)
# --------------------------------------------------------------------------- #
def _triggers_fake() -> FakeControlPlaneClient:
    fake = FakeControlPlaneClient()
    fake.triggers = [
        {
            "id": "nightly-scan",
            "tenant_id": "default",
            "source": "cron",
            "target": {"kind": "workload", "ref": "agent-a"},
            "schedule": "0 3 * * *",
            "admission_rule": "staging-auto",
            "cooldown_seconds": 60,
            "dedup": {"key": "{{ repo }}", "window_minutes": 30},
        },
        {
            "id": "pr-webhook",
            "tenant_id": "default",
            "source": "github",
            "target": {"kind": "workload", "ref": "agent-b"},
            "schedule": None,
            "admission_rule": "gated",
            "cooldown_seconds": 0,
            "dedup": None,
        },
    ]
    return fake


def test_triggers_renders_table() -> None:
    fake = _triggers_fake()

    response = _app(fake).get("/triggers")

    assert response.status_code == 200
    assert "nightly-scan" in response.text
    assert "cron" in response.text
    assert "agent-a" in response.text
    assert "0 3 * * *" in response.text
    assert "pr-webhook" in response.text
    assert "github" in response.text
    assert ("list_triggers", ()) in fake.calls


def test_triggers_empty_state() -> None:
    response = _app(FakeControlPlaneClient()).get("/triggers")

    assert response.status_code == 200
    assert "No triggers" in response.text


def _queue_fake() -> FakeControlPlaneClient:
    fake = FakeControlPlaneClient()
    fake.queue = {
        "depth": 3,
        "by_qos": {"interactive": 1, "batch": 2},
        "by_priority": {"high": 2, "low": 1},
        "waiting": [
            {
                "task_id": "t1",
                "workload": "agent-a",
                "qos": "interactive",
                "priority": 9,
                "reason": "awaiting capacity",
            },
            {
                "task_id": "t2",
                "workload": "agent-b",
                "qos": "batch",
                "priority": 1,
                "reason": "cooldown",
            },
        ],
        "running_by_workload": {"agent-a": 2},
    }
    return fake


def test_queue_renders_depth_breakdowns_and_waiting() -> None:
    fake = _queue_fake()

    response = _app(fake).get("/queue")

    assert response.status_code == 200
    assert ">3<" in response.text
    assert "t1" in response.text
    assert "t2" in response.text
    assert "cooldown" in response.text
    assert "interactive" in response.text
    assert "agent-a" in response.text
    assert ("get_queue", ()) in fake.calls


def test_queue_empty_state() -> None:
    response = _app(FakeControlPlaneClient()).get("/queue")

    assert response.status_code == 200
    assert "No waiting" in response.text


def test_nav_includes_triggers_and_queue_links() -> None:
    response = _app(FakeControlPlaneClient()).get("/")

    assert 'href="/triggers"' in response.text
    assert 'href="/queue"' in response.text


# --------------------------------------------------------------------------- #
# Login / logout and RBAC gating (M52)
# --------------------------------------------------------------------------- #
def test_login_sets_session_cookie() -> None:
    fake = FakeControlPlaneClient()
    fake.identity = {
        "operator_id": "alice",
        "tenant_id": "default",
        "role": "approver",
        "method": "api_key",
        "scopes": [],
    }
    client = TestClient(create_ui_app(client=fake), follow_redirects=False)

    response = client.post("/login", data={"api_key": "k1"})

    assert response.status_code == 303
    assert ("with_token", ("k1",)) in fake.calls
    assert ("whoami", ()) in fake.calls
    assert SESSION_COOKIE in response.cookies
    session = verify_session(
        response.cookies[SESSION_COOKIE], get_settings().ui.session_secret
    )
    assert session is not None
    assert session.operator_id == "alice"
    assert session.role == Role.APPROVER
    assert session.api_key == "k1"


def test_login_rejects_bad_key() -> None:
    fake = FakeControlPlaneClient()
    fake.errors["whoami"] = ControlPlaneError(401, "invalid key")
    client = TestClient(create_ui_app(client=fake), follow_redirects=False)

    response = client.post("/login", data={"api_key": "bad"})

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")
    assert SESSION_COOKIE not in response.cookies


def _approver_client() -> TestClient:
    fake = FakeControlPlaneClient()
    fake.identity = {
        "operator_id": "alice",
        "tenant_id": "default",
        "role": "approver",
        "method": "api_key",
        "scopes": [],
    }
    return TestClient(create_ui_app(client=fake), follow_redirects=False)


def test_login_cookie_is_secure_by_default() -> None:
    response = _approver_client().post("/login", data={"api_key": "k1"})

    assert response.status_code == 303
    assert "secure" in response.headers["set-cookie"].lower()


def test_login_session_authenticates_followup_when_auth_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HIVEPLANE_AUTH__ENABLED", "true")
    monkeypatch.setenv("HIVEPLANE_UI__SESSION_COOKIE_SECURE", "false")
    monkeypatch.setenv("HIVEPLANE_UI__SESSION_SECRET", "test-ui-secret")
    get_settings.cache_clear()
    client = _approver_client()

    login = client.post("/login", data={"api_key": "k1"})

    assert login.status_code == 303
    assert SESSION_COOKIE in login.cookies
    assert client.get("/").status_code == 200


def test_default_session_secret_rejected_with_auth_enabled(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HIVEPLANE_AUTH__ENABLED", "true")
    get_settings.cache_clear()

    with pytest.raises(RuntimeError):
        create_ui_app(client=FakeControlPlaneClient())


def test_forged_session_cookie_is_rejected_with_custom_secret(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from hiveplane.config import DEFAULT_UI_SESSION_SECRET
    from hiveplane.ui.session import UiSession, sign_session

    monkeypatch.setenv("HIVEPLANE_AUTH__ENABLED", "true")
    monkeypatch.setenv("HIVEPLANE_UI__SESSION_SECRET", "private-ui-secret")
    get_settings.cache_clear()
    forged = sign_session(
        UiSession(
            operator_id="mallory",
            tenant_id="default",
            role=Role.ADMIN,
            api_key="forged-key",
            exp=9_999_999_999,
        ),
        DEFAULT_UI_SESSION_SECRET,
    )
    client = TestClient(create_ui_app(client=FakeControlPlaneClient()), follow_redirects=False)
    client.cookies.set(SESSION_COOKIE, forged)

    assert client.get("/").status_code == 401


def test_login_rejects_malformed_whoami() -> None:
    fake = FakeControlPlaneClient()
    fake.identity = {"tenant_id": "default"}
    client = TestClient(create_ui_app(client=fake), follow_redirects=False)

    response = client.post("/login", data={"api_key": "k1"})

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")
    assert SESSION_COOKIE not in response.cookies


def test_login_rejects_empty_operator_id() -> None:
    fake = FakeControlPlaneClient()
    fake.identity = {"operator_id": "", "role": "viewer", "tenant_id": "default"}
    client = TestClient(create_ui_app(client=fake), follow_redirects=False)

    response = client.post("/login", data={"api_key": "k1"})

    assert response.status_code == 303
    assert response.headers["location"].startswith("/login")
    assert SESSION_COOKIE not in response.cookies


def test_logout_clears_session_cookie() -> None:
    client = TestClient(create_ui_app(client=FakeControlPlaneClient()), follow_redirects=False)
    client.cookies.set(SESSION_COOKIE, session_token(Role.ADMIN))

    response = client.post("/logout")

    assert response.status_code == 303
    assert response.cookies.get(SESSION_COOKIE, "") == ""


def test_viewer_is_denied_approve() -> None:
    fake = _approvals_fake()
    client = TestClient(create_ui_app(client=fake), follow_redirects=False)
    client.cookies.set(SESSION_COOKIE, session_token(Role.VIEWER))

    response = client.post("/approvals/a1/approve", data={"operator": "v"})

    assert response.status_code == 403
    assert not any(name == "approve" for name, _ in fake.calls)


def test_approver_is_allowed_to_deny() -> None:
    fake = _approvals_fake()
    client = TestClient(create_ui_app(client=fake), follow_redirects=False)
    client.cookies.set(SESSION_COOKIE, session_token(Role.APPROVER))

    response = client.post(
        "/approvals/a1/deny", data={"operator": "alice", "reason": "risky"}
    )

    assert response.status_code == 303
    assert ("with_token", ("hp-key-1",)) in fake.calls
    assert ("deny", ("a1", "alice", "risky")) in fake.calls


def test_session_tenant_is_forwarded_to_client() -> None:
    fake = FakeControlPlaneClient()
    client = TestClient(create_ui_app(client=fake), follow_redirects=False)
    client.cookies.set(SESSION_COOKIE, session_token(Role.ADMIN, tenant_id="other"))

    response = client.get("/")

    assert response.status_code == 200
    assert fake.token == "hp-key-1"
    assert fake.tenant_id == "other"


def test_anonymous_admin_when_auth_disabled() -> None:
    fake = _approvals_fake()

    response = _app(fake).post("/approvals/a1/approve", data={"operator": "anon"})

    assert response.status_code == 303
    assert ("approve", ("a1", "anonymous", None)) in fake.calls


def test_login_form_renders() -> None:
    response = _app(FakeControlPlaneClient()).get("/login")

    assert response.status_code == 200
    assert 'action="/login"' in response.text
    assert 'name="api_key"' in response.text


def test_invalid_session_cookie_falls_back_to_anonymous_admin() -> None:
    client = TestClient(create_ui_app(client=FakeControlPlaneClient()), follow_redirects=False)
    client.cookies.set(SESSION_COOKIE, "tampered")

    response = client.get("/")

    assert response.status_code == 200


def test_auth_enabled_requires_login(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HIVEPLANE_AUTH__ENABLED", "true")
    monkeypatch.setenv("HIVEPLANE_UI__SESSION_SECRET", "test-ui-secret")
    get_settings.cache_clear()

    response = _app(FakeControlPlaneClient()).get("/")

    assert response.status_code == 401


def test_certifications_tolerate_quarantine_outage() -> None:
    fake = _certs_fake()
    fake.errors["list_quarantines"] = ControlPlaneError(503, "down")

    response = _app(fake).get("/certifications")

    assert response.status_code == 200
    assert "agent-a" in response.text


def test_nav_hides_approvals_link_for_viewer() -> None:
    client = TestClient(create_ui_app(client=FakeControlPlaneClient()), follow_redirects=False)
    client.cookies.set(SESSION_COOKIE, session_token(Role.VIEWER))

    response = client.get("/")

    assert response.status_code == 200
    assert 'href="/approvals"' not in response.text


def test_nav_shows_approvals_link_for_approver() -> None:
    client = TestClient(create_ui_app(client=FakeControlPlaneClient()), follow_redirects=False)
    client.cookies.set(SESSION_COOKIE, session_token(Role.APPROVER))

    response = client.get("/")

    assert 'href="/approvals"' in response.text


# --------------------------------------------------------------------------- #
# Diff viewer (M52, #402)
# --------------------------------------------------------------------------- #
def test_diff_version_route_dispatches() -> None:
    fake = FakeControlPlaneClient()
    fake.version_diff = {
        "workload": "agent-a",
        "from_version": 1,
        "to_version": 2,
        "changed_fields": ["model"],
        "re_certification_required": True,
    }

    response = _app(fake).get(
        "/diff?kind=version&name=agent-a&from_version=1&to_version=2"
    )

    assert response.status_code == 200
    assert "agent-a v1→v2" in response.text
    assert ("get_version_diff", ("agent-a", 1, 2)) in fake.calls


def test_diff_regression_route_dispatches() -> None:
    fake = FakeControlPlaneClient()
    fake.cert_comparison = {
        "workload_id": "agent-a",
        "before_attestation_id": "att-1",
        "after_attestation_id": "att-2",
        "regressed": [{"task_id": "t1", "before": "pass", "after": "fail"}],
        "improved": [],
        "blocked": True,
        "severity": "high",
        "summary": "regressed",
    }

    response = _app(fake).get("/diff?kind=regression&before_id=att-1&after_id=att-2")

    assert response.status_code == 200
    assert "agent-a regression" in response.text
    assert "t1" in response.text
    assert ("compare_certifications", ("att-1", "att-2")) in fake.calls


def test_diff_runs_route_dispatches() -> None:
    fake = FakeControlPlaneClient()
    fake.stories = {
        "r1": {"run_id": "r1", "state": "running", "cost_usd": 1.0},
        "r2": {"run_id": "r2", "state": "completed", "cost_usd": 2.0},
    }

    response = _app(fake).get("/diff?kind=runs&before_id=r1&after_id=r2")

    assert response.status_code == 200
    assert "run r1→r2" in response.text
    assert ("get_story", ("r1",)) in fake.calls
    assert ("get_story", ("r2",)) in fake.calls


def test_diff_defaults_to_version_form_with_empty_state() -> None:
    response = _app(FakeControlPlaneClient()).get("/diff")

    assert response.status_code == 200
    assert 'name="kind"' in response.text
    assert "No diff" in response.text


def test_diff_unknown_kind_renders_empty_state() -> None:
    response = _app(FakeControlPlaneClient()).get("/diff?kind=bogus")

    assert response.status_code == 200
    assert "No diff" in response.text


def test_diff_bad_params_render_empty_state() -> None:
    response = _app(FakeControlPlaneClient()).get(
        "/diff?kind=version&name=agent-a&from_version=x&to_version=2"
    )

    assert response.status_code == 200
    assert "No diff" in response.text


def test_nav_includes_diff_link() -> None:
    response = _app(FakeControlPlaneClient()).get("/")

    assert 'href="/diff"' in response.text


# --------------------------------------------------------------------------- #
# Global search (M52, #403)
# --------------------------------------------------------------------------- #
def _search_fake() -> FakeControlPlaneClient:
    fake = FakeControlPlaneClient()
    fake.search_hits = [
        {"kind": "run", "identifier": "r1", "label": "agent-a run"},
        {"kind": "approval", "identifier": "a1", "label": "delete requested"},
        {"kind": "workload", "identifier": "agent-a", "label": "agent-a workload"},
    ]
    return fake


def _result_row(html: str, label: str) -> str:
    """Return the results-table row containing ``label``, excluding the nav."""
    body = html.split("<tbody>", 1)[1].split("</tbody>", 1)[0]
    matches = [row for row in body.split("<tr>") if label in row]
    assert len(matches) == 1, f"expected one row for {label!r}, got {len(matches)}"
    return matches[0]


def test_search_renders_hits_with_kind_links() -> None:
    fake = _search_fake()

    response = _app(fake).get("/search?q=agent")

    assert response.status_code == 200
    assert "agent-a run" in response.text
    assert "delete requested" in response.text
    assert "agent-a workload" in response.text
    run_row = _result_row(response.text, "agent-a run")
    assert 'href="/runs/r1"' in run_row
    approval_row = _result_row(response.text, "delete requested")
    assert 'href="/approvals"' in approval_row
    workload_row = _result_row(response.text, "agent-a workload")
    assert 'href="/"' in workload_row
    assert ("search", ("agent", 20)) in fake.calls


def test_search_workload_hit_links_to_fleet_not_workloads() -> None:
    response = _app(_search_fake()).get("/search?q=agent")

    assert 'href="/workloads"' not in response.text
    workload_row = _result_row(response.text, "agent-a workload")
    assert 'href="/"' in workload_row


def test_search_escapes_hostile_query_and_label() -> None:
    fake = FakeControlPlaneClient()
    fake.search_hits = [
        {"kind": "run", "identifier": "r1", "label": "<script>alert(1)</script>"}
    ]
    client = TestClient(create_ui_app(client=fake), follow_redirects=False)

    response = client.get("/search", params={"q": "<script>"})

    assert response.status_code == 200
    assert "<script>" not in response.text
    assert "&lt;script&gt;" in response.text
    assert "alert(1)" in response.text


def test_search_empty_query_renders_prompt_state() -> None:
    fake = _search_fake()

    response = _app(fake).get("/search")

    assert response.status_code == 200
    assert "Enter a query" in response.text
    assert not any(name == "search" for name, _ in fake.calls)


def test_search_no_matches_renders_empty_state() -> None:
    response = _app(FakeControlPlaneClient()).get("/search?q=zzz")

    assert response.status_code == 200
    assert "No matches" in response.text
    assert "Enter a query" not in response.text


def test_search_control_plane_outage_renders_502() -> None:
    fake = _search_fake()
    fake.errors["search"] = ControlPlaneError(503, "control plane down")

    response = TestClient(create_ui_app(client=fake), raise_server_exceptions=False).get(
        "/search?q=agent"
    )

    assert response.status_code == 502
    assert "control plane down" in response.text


def test_nav_includes_search_link() -> None:
    response = _app(FakeControlPlaneClient()).get("/")

    assert 'href="/search"' in response.text


# --------------------------------------------------------------------------- #
# Onboarding wizard (M52, #404)
# --------------------------------------------------------------------------- #
def _onboarded_fake() -> FakeControlPlaneClient:
    fake = FakeControlPlaneClient()
    fake.adapters = [{"name": "openai", "kind": "model", "status": "connected"}]
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
        }
    ]
    fake.triggers = [
        {
            "id": "nightly-scan",
            "tenant_id": "default",
            "source": "cron",
            "target": {"kind": "workload", "ref": "agent-a"},
            "schedule": "0 3 * * *",
            "admission_rule": "staging-auto",
            "cooldown_seconds": 60,
            "dedup": None,
        }
    ]
    return fake


def test_onboarding_empty_state_counts_zero_done() -> None:
    response = _app(FakeControlPlaneClient()).get("/onboarding")

    assert response.status_code == 200
    assert "0 of 4 complete" in response.text


def test_onboarding_derives_all_four_steps_done() -> None:
    response = _app(_onboarded_fake()).get("/onboarding")

    assert response.status_code == 200
    assert "4 of 4 complete" in response.text
    assert "All steps complete" in response.text


def test_onboarding_partial_completion() -> None:
    fake = FakeControlPlaneClient()
    fake.adapters = [{"name": "openai"}]
    fake.workloads = _onboarded_fake().workloads

    response = _app(fake).get("/onboarding")

    assert response.status_code == 200
    assert "2 of 4 complete" in response.text


def test_onboarding_ignores_uncertified_records() -> None:
    fake = _onboarded_fake()
    fake.certifications[0]["certification"]["status"] = "quarantined"

    response = _app(fake).get("/onboarding")

    assert response.status_code == 200
    assert "3 of 4 complete" in response.text


def test_onboarding_renders_step_links() -> None:
    response = _app(FakeControlPlaneClient()).get("/onboarding")

    assert response.status_code == 200
    assert 'href="/settings/model"' in response.text
    assert 'href="/workloads"' in response.text
    assert 'href="/certifications"' in response.text
    assert 'href="/triggers"' in response.text


def test_onboarding_queries_live_state() -> None:
    fake = _onboarded_fake()

    _app(fake).get("/onboarding")

    assert ("list_adapters", ()) in fake.calls
    assert ("list_workloads", ()) in fake.calls
    assert ("list_certifications", (None, None)) in fake.calls
    assert ("list_triggers", ()) in fake.calls


def test_nav_includes_onboarding_link() -> None:
    response = _app(FakeControlPlaneClient()).get("/")

    assert 'href="/onboarding"' in response.text


# --------------------------------------------------------------------------- #
# Replay (M60)
# --------------------------------------------------------------------------- #
def test_replay_route_renders_frames() -> None:
    fake = FakeControlPlaneClient()
    fake.replay = {
        "run_id": "r1",
        "digest": "deadbeef",
        "side_effects": False,
        "frames": [
            {"sequence": 0, "event_type": "admission", "detail": "allowed"},
        ],
    }

    response = _app(fake).get("/replay/r1")

    assert response.status_code == 200
    assert "admission" in response.text
    assert "deadbeef" in response.text
    assert ("get_replay", ("r1",)) in fake.calls


def test_diff_replay_route_dispatches() -> None:
    fake = FakeControlPlaneClient()
    fake.replay_diff_result = {
        "source_run_id": "r1",
        "target_run_id": "r2",
        "identical": False,
        "field_deltas": [{"field": "cost_usd", "before": 1.0, "after": 2.0}],
    }

    response = _app(fake).get("/diff?kind=replay&before_id=r1&after_id=r2")

    assert response.status_code == 200
    assert "run r1→r2" in response.text
    assert ("replay_diff", ("r1", "r2")) in fake.calls
