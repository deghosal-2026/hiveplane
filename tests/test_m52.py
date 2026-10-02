"""Tests for operator UI v2 view models and RBAC gating (M52)."""

from __future__ import annotations

import base64
import hashlib
import hmac
from typing import Any

import pytest
from fastapi.testclient import TestClient

from hiveplane.auth.models import Permission
from hiveplane.tenancy.models import Role
from hiveplane.ui import app as ui_app
from hiveplane.ui.app import create_ui_app
from hiveplane.ui.client import ControlPlaneError
from hiveplane.ui.rbac import UiIdentity, can, visible_actions
from hiveplane.ui.session import UiSession, sign_session, verify_session
from hiveplane.ui.stream import run_event_frames
from hiveplane.ui.views import (
    build_diff,
    build_health,
    build_health_from_api,
    build_onboarding,
    build_queue,
    build_roi,
    build_search,
    diff_from_regression,
    diff_from_runs,
    diff_from_version_diff,
)
from ui_fakes import FakeControlPlaneClient


# --------------------------------------------------------------------------- #
# M52-05 — queue visualizer
# --------------------------------------------------------------------------- #
def test_build_queue_view() -> None:
    view = build_queue(
        {
            "depth": 2,
            "by_qos": {"best_effort": 1, "guaranteed": 1},
            "by_priority": {"0": 1, "5": 1},
            "waiting": [
                {
                    "task_id": "t1",
                    "workload": "repo-agent",
                    "qos": "guaranteed",
                    "priority": 5,
                    "reason": "awaiting capacity",
                }
            ],
            "running_by_workload": {"repo-agent": 1},
        }
    )
    assert view.depth == 2
    assert view.by_qos["guaranteed"] == 1
    assert view.items[0].task_id == "t1"
    assert view.running_by_workload == {"repo-agent": 1}


# --------------------------------------------------------------------------- #
# M52-04 — health and ROI dashboards
# --------------------------------------------------------------------------- #
def test_build_health_view() -> None:
    view = build_health(
        [
            {"workload": "a", "status": "healthy", "success_rate": 0.99},
            {"workload": "b", "status": "degraded", "error_budget_remaining": 0.1},
        ]
    )
    assert view.healthy == 1
    assert view.degraded == 1
    assert {row.workload for row in view.workloads} == {"a", "b"}


def test_build_health_from_api_maps_failure_rate_and_availability_budget() -> None:
    view = build_health_from_api(
        [
            {
                "workload": "a",
                "failure_rate": 0.02,
                "status": "healthy",
                "objectives": [
                    {"objective": "latency", "remaining": 0.9},
                    {"objective": "availability", "remaining": 0.75},
                ],
            },
            {
                "workload": "b",
                "failure_rate": 0.5,
                "status": "degraded",
                "objectives": [],
            },
        ]
    )
    by_name = {row.workload: row for row in view.workloads}
    assert by_name["a"].success_rate == pytest.approx(0.98)
    assert by_name["a"].error_budget_remaining == pytest.approx(0.75)
    assert by_name["b"].success_rate == pytest.approx(0.5)
    assert by_name["b"].error_budget_remaining == 0.0
    assert view.healthy == 1
    assert view.degraded == 1


def test_build_health_from_api_tolerates_missing_objectives_key() -> None:
    view = build_health_from_api([{"workload": "a", "status": "healthy"}])

    row = view.workloads[0]
    assert row.success_rate == pytest.approx(1.0)
    assert row.error_budget_remaining == 0.0


def test_build_roi_view_flags() -> None:
    view = build_roi(
        {
            "fleet_roi": 1.5,
            "total_spend_usd": 60.0,
            "rows": [
                {
                    "workload_id": "bad",
                    "spend_usd": 50.0,
                    "value_usd": 5.0,
                    "roi": 0.1,
                    "expensive_low_value": True,
                    "evidence": ["spend high"],
                }
            ],
        }
    )
    assert view.fleet_roi == 1.5
    assert [row.workload for row in view.flagged] == ["bad"]
    assert view.flagged[0].evidence == ["spend high"]


# --------------------------------------------------------------------------- #
# M52-07 — global search
# --------------------------------------------------------------------------- #
def test_build_search_view() -> None:
    view = build_search(
        "run-42",
        [
            {"kind": "run", "identifier": "run-42", "label": "agent-1"},
            {"kind": "approval", "identifier": "apv-1"},
        ],
    )
    assert view.query == "run-42"
    assert {hit.kind for hit in view.hits} == {"run", "approval"}


# --------------------------------------------------------------------------- #
# M52-06/08 — diff and onboarding
# --------------------------------------------------------------------------- #
def test_build_diff_view() -> None:
    view = build_diff(
        {
            "title": "regression diff",
            "entries": [{"field": "model", "before": "gpt-4o", "after": "gpt-4o-mini"}],
        }
    )
    assert view.entries[0].before == "gpt-4o"


def test_diff_from_version_diff_maps_changed_fields() -> None:
    view = diff_from_version_diff(
        {
            "workload": "agent-a",
            "from_version": 1,
            "to_version": 2,
            "changed_fields": ["model", "temperature"],
            "re_certification_required": True,
        }
    )
    assert view.title == "agent-a v1→v2"
    assert [(entry.field, entry.before, entry.after) for entry in view.entries] == [
        ("model", "—", "changed"),
        ("temperature", "—", "changed"),
    ]


def test_diff_from_version_diff_tolerates_empty_payload() -> None:
    view = diff_from_version_diff({})
    assert view.entries == []
    assert view.title == " v→v"


def test_diff_from_regression_maps_regressed_and_improved() -> None:
    view = diff_from_regression(
        {
            "workload_id": "agent-a",
            "before_attestation_id": "att-1",
            "after_attestation_id": "att-2",
            "regressed": [
                {"task_id": "t1", "before": "pass", "after": "fail", "critical": True}
            ],
            "improved": [{"task_id": "t2", "before": "fail", "after": "pass"}],
            "blocked": True,
            "severity": "high",
            "summary": "1 regression",
        }
    )
    assert view.title == "agent-a regression"
    assert [(entry.field, entry.before, entry.after) for entry in view.entries] == [
        ("t1", "pass", "fail"),
        ("t2", "fail", "pass"),
    ]


def test_diff_from_regression_empty() -> None:
    view = diff_from_regression({})
    assert view.title == " regression"
    assert view.entries == []


def test_diff_from_runs_compares_top_level_fields() -> None:
    view = diff_from_runs(
        {
            "run_id": "r1",
            "state": "running",
            "cost_usd": 1.0,
            "model_identity": "gpt-4o",
        },
        {
            "run_id": "r2",
            "state": "completed",
            "cost_usd": 2.5,
            "model_identity": "gpt-4o",
        },
    )
    assert view.title == "run r1→r2"
    assert [(entry.field, entry.before, entry.after) for entry in view.entries] == [
        ("state", "running", "completed"),
        ("cost_usd", "1.0", "2.5"),
    ]


def test_diff_from_runs_identical_yields_no_entries() -> None:
    story = {"run_id": "r1", "state": "completed", "cost_usd": 1.0}
    view = diff_from_runs(story, dict(story))
    assert view.entries == []


def test_build_onboarding_view() -> None:
    view = build_onboarding(done={"connect", "register"})
    assert view.completed == 2
    assert [step.key for step in view.steps] == ["connect", "register", "certify", "trigger"]
    assert view.steps[0].done is True and view.steps[2].done is False


# --------------------------------------------------------------------------- #
# M52-01 — RBAC-gated actions
# --------------------------------------------------------------------------- #
def test_viewer_sees_no_privileged_controls() -> None:
    actions = visible_actions(UiIdentity(role=Role.VIEWER))
    assert actions == {
        "approve": False,
        "promote": False,
        "kill_switch": False,
        "intervene": False,
        "secrets_manage": False,
    }


def test_approver_and_admin_controls() -> None:
    approver = visible_actions(UiIdentity(role=Role.APPROVER))
    assert approver["approve"] is True
    assert approver["promote"] is False and approver["kill_switch"] is False

    admin = visible_actions(UiIdentity(role=Role.ADMIN))
    assert all(admin.values())
    assert can(UiIdentity(role=Role.ADMIN), Permission.KILL_SWITCH) is True
    assert can(UiIdentity(role=Role.VIEWER), Permission.APPROVE) is False


def _session(exp: int = 9_999_999_999) -> UiSession:
    return UiSession(
        operator_id="alice",
        tenant_id="default",
        role=Role.ADMIN,
        api_key="hp-key-1",
        exp=exp,
    )


def test_session_round_trip() -> None:
    token = sign_session(_session(), "secret")
    assert verify_session(token, "secret") == _session()


def test_session_rejects_tampered_token() -> None:
    token = sign_session(_session(), "secret")
    assert verify_session(token + "x", "secret") is None
    assert verify_session(token, "other-secret") is None


def test_session_rejects_expired_and_malformed() -> None:
    assert verify_session(sign_session(_session(exp=1), "s"), "s", now=100) is None
    assert verify_session("not-a-token", "s") is None


def test_session_rejects_garbage_payload_with_valid_signature() -> None:
    payload = base64.urlsafe_b64encode(b"not-json").decode().rstrip("=")
    digest = hmac.new(b"s", payload.encode(), hashlib.sha256).digest()
    signature = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    assert verify_session(f"{payload}.{signature}", "s") is None


def test_session_rejects_non_ascii_signature() -> None:
    assert verify_session("abc.\u0434", "s") is None


# --------------------------------------------------------------------------- #
# M52-02 — live run view via SSE
# --------------------------------------------------------------------------- #
def test_run_event_frames_only_new_events() -> None:
    events = [{"sequence": 1, "type": "state_change"}, {"sequence": 2, "type": "usage"}]

    frames = list(run_event_frames(events, seen=1))

    assert len(frames) == 1
    assert '"sequence": 2' in frames[0]
    assert frames[0].startswith("event: run")


def test_run_event_frames_orders_by_sequence_and_skips_seen() -> None:
    events = [
        {"sequence": 3, "type": "usage"},
        {"sequence": 1, "type": "state_change"},
        {"sequence": 2, "type": "approval"},
    ]

    frames = list(run_event_frames(events, seen=1))

    assert len(frames) == 2
    assert '"sequence": 2' in frames[0]
    assert '"sequence": 3' in frames[1]
    assert '"sequence": 1' not in frames[0]
    assert frames[0].endswith("\n\n")


def test_run_event_frames_tolerates_missing_or_bad_sequence() -> None:
    events: list[dict[str, Any]] = [
        {"sequence": None, "type": "state_change"},
        {"type": "usage"},
        {"sequence": "bad", "type": "tool_call"},
    ]

    frames = list(run_event_frames(events, seen=-1))

    assert len(frames) == 3


def _terminal_stream_client() -> TestClient:
    fake = FakeControlPlaneClient()
    fake.runs = [{"id": "r1", "state": "completed"}]
    fake.run_events = [
        {"sequence": 1, "type": "state_change", "summary": "queued"},
        {"sequence": 2, "type": "usage", "summary": "5 tokens"},
    ]
    return TestClient(create_ui_app(client=fake), follow_redirects=False)


def test_run_stream_emits_events_and_ends_on_terminal_run() -> None:
    response = _terminal_stream_client().get("/runs/r1/stream")

    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/event-stream")
    assert "event: run" in response.text
    assert '"sequence": 2' in response.text
    assert "event: end" in response.text


def test_run_stream_honours_seen_query_param() -> None:
    response = _terminal_stream_client().get("/runs/r1/stream?seen=1")

    assert response.status_code == 200
    assert response.text.count("event: run") == 1
    assert '"sequence": 1' not in response.text
    assert '"sequence": 2' in response.text


def test_run_stream_ends_on_control_plane_error() -> None:
    fake = FakeControlPlaneClient()
    fake.runs = [{"id": "r1", "state": "running"}]
    fake.errors["get_run_events"] = ControlPlaneError(503, "control plane down")
    client = TestClient(create_ui_app(client=fake), follow_redirects=False)

    response = client.get("/runs/r1/stream")

    assert response.status_code == 200
    assert response.text == "event: end\ndata: {}\n\n"


def test_run_stream_ends_when_terminal_probe_fails() -> None:
    fake = FakeControlPlaneClient()
    fake.run_events = [{"sequence": 1, "type": "state_change"}]
    fake.errors["get_run"] = ControlPlaneError(503, "control plane down")
    client = TestClient(create_ui_app(client=fake), follow_redirects=False)

    response = client.get("/runs/r1/stream")

    assert response.status_code == 200
    assert "event: run" in response.text
    assert response.text.endswith("event: end\ndata: {}\n\n")


def test_run_stream_emits_end_when_no_events() -> None:
    fake = FakeControlPlaneClient()
    fake.runs = [{"id": "r1", "state": "completed"}]
    client = TestClient(create_ui_app(client=fake), follow_redirects=False)

    response = client.get("/runs/r1/stream")

    assert response.status_code == 200
    assert response.text == "event: end\ndata: {}\n\n"


def test_run_stream_heartbeats_until_duration_cap(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    fake = FakeControlPlaneClient()
    fake.runs = [{"id": "r1", "state": "running"}]
    fake.run_events = [{"sequence": 1, "type": "state_change", "summary": "queued"}]
    monkeypatch.setattr(ui_app, "STREAM_MAX_SECONDS", 0.05)
    monkeypatch.setattr(ui_app, "STREAM_POLL_SECONDS", 0.0)
    client = TestClient(create_ui_app(client=fake), follow_redirects=False)

    response = client.get("/runs/r1/stream")

    assert response.status_code == 200
    assert ": heartbeat" in response.text
    assert "event: end" in response.text
    assert response.text.count("event: run") == 1


def test_run_detail_renders_live_timeline_container() -> None:
    fake = FakeControlPlaneClient()
    fake.story = {
        "run_id": "r1",
        "state": "running",
        "entries": [
            {
                "timestamp": "2026-09-19T11:00:00Z",
                "kind": "state",
                "summary": "queued -> running",
                "detail": {"sequence": 7},
            }
        ],
    }

    response = TestClient(create_ui_app(client=fake)).get("/runs/r1")

    assert response.status_code == 200
    assert 'id="run-timeline"' in response.text
    assert 'data-sequence="7"' in response.text
    assert "new EventSource" in response.text
    assert "/stream?seen=" in response.text
    assert 'event.type === "state_change"' in response.text
    assert '(event.from_state || "?") + " -> " + (event.to_state || "?")' in response.text
