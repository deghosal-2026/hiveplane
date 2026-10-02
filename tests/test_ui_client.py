"""Tests for the operator UI control-plane client (M22, #84)."""

from __future__ import annotations

import json
from collections.abc import Callable
from typing import Any

import httpx
import pytest

from hiveplane.ui.client import ControlPlaneError, HttpControlPlaneClient

Handler = Callable[[httpx.Request], httpx.Response]


def _client(handler: Handler) -> HttpControlPlaneClient:
    transport = httpx.MockTransport(handler)
    return HttpControlPlaneClient("http://cp", client=httpx.Client(transport=transport))


def _json_handler(payload: Any) -> Handler:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return handler


def test_list_workloads_gets_catalog() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/workloads"
        return httpx.Response(200, json=[{"name": "agent-a"}])

    assert _client(handler).list_workloads() == [{"name": "agent-a"}]


def test_get_workload_encodes_name() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.raw_path.decode() == "/workloads/agent%20a"
        return httpx.Response(200, json={"name": "agent a"})

    assert _client(handler).get_workload("agent a") == {"name": "agent a"}


def test_list_runs_passes_filters() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/runs"
        assert request.url.params["workload"] == "agent-a"
        assert request.url.params["state"] == "failed"
        return httpx.Response(200, json=[])

    assert _client(handler).list_runs(workload="agent-a", state="failed") == []


def test_list_runs_omits_absent_filters() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert dict(request.url.params) == {}
        return httpx.Response(200, json=[])

    assert _client(handler).list_runs() == []


def test_get_run_and_story() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json={"id": "r1"})

    client = _client(handler)
    assert client.get_run("r1") == {"id": "r1"}
    assert client.get_story("r1") == {"id": "r1"}
    assert calls == ["/runs/r1", "/runs/r1/story"]


def test_list_approvals_passes_filters() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/approvals"
        assert request.url.params["status"] == "pending"
        assert request.url.params["workload"] == "agent-a"
        return httpx.Response(200, json=[])

    assert _client(handler).list_approvals(status="pending", workload="agent-a") == []


def test_approve_sends_operator_and_reason() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/approvals/a1/approve"
        assert json.loads(request.content) == {"operator": "alice", "reason": "lgtm"}
        return httpx.Response(200, json={"approval_id": "a1", "status": "approved"})

    result = _client(handler).approve("a1", "alice", "lgtm")

    assert result["status"] == "approved"


def test_approve_omits_absent_reason() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert json.loads(request.content) == {"operator": "alice"}
        return httpx.Response(200, json={"approval_id": "a1", "status": "approved"})

    _client(handler).approve("a1", "alice")


def test_deny_sends_operator_and_reason() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/approvals/a1/deny"
        assert json.loads(request.content) == {"operator": "bob", "reason": "risky"}
        return httpx.Response(200, json={"approval_id": "a1", "status": "denied"})

    assert _client(handler).deny("a1", "bob", "risky")["status"] == "denied"


@pytest.mark.parametrize("action", ["pause", "resume", "stop"])
def test_interventions_post_to_run_action(action: str) -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == f"/runs/r1/{action}"
        return httpx.Response(200, json={"id": "r1", "state": "x"})

    result = getattr(_client(handler), action)("r1")

    assert result["id"] == "r1"


def test_list_certifications_passes_filters() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/certifications"
        assert request.url.params["workload"] == "agent-a"
        assert request.url.params["status"] == "certified"
        return httpx.Response(200, json=[])

    assert _client(handler).list_certifications(workload="agent-a", status="certified") == []


def test_get_spend_gets_summary() -> None:
    payload: dict[str, Any] = {"by_workload": [], "by_team": []}
    assert _client(_json_handler(payload)).get_spend() == payload


def test_non_2xx_raises_with_status_and_detail() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(409, json={"detail": "run is not paused"})

    with pytest.raises(ControlPlaneError) as excinfo:
        _client(handler).pause("r1")

    assert excinfo.value.status_code == 409
    assert excinfo.value.detail == "run is not paused"


def test_server_error_raises_control_plane_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(500, text="boom")

    with pytest.raises(ControlPlaneError) as excinfo:
        _client(handler).list_workloads()

    assert excinfo.value.status_code == 500


def test_error_detail_from_non_dict_json_body() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(422, json=["bad", "request"])

    with pytest.raises(ControlPlaneError) as excinfo:
        _client(handler).list_workloads()

    assert excinfo.value.detail == "['bad', 'request']"


def test_transport_error_maps_to_control_plane_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(ControlPlaneError) as excinfo:
        _client(handler).list_workloads()

    assert excinfo.value.status_code is None
    assert "connection refused" in excinfo.value.detail


def test_record_feedback_sends_verdict_notes_and_operator() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/runs/r1/feedback"
        assert json.loads(request.content) == {
            "verdict": "failed-with-lesson",
            "notes": "escalate",
            "operator": "alice",
        }
        return httpx.Response(201, json={"feedback_id": "fb-1"})

    result = _client(handler).record_feedback(
        "r1", "failed-with-lesson", "escalate", "alice"
    )

    assert result["feedback_id"] == "fb-1"


def test_with_token_forwards_bearer() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["Authorization"] == "Bearer t1"
        assert request.url.path == "/workloads"
        return httpx.Response(200, json=[])

    assert _client(handler).with_token("t1").list_workloads() == []


def test_with_token_clone_without_token_omits_authorization() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "Authorization" not in request.headers
        return httpx.Response(200, json=[])

    assert _client(handler).with_token("t1").with_token(None).list_workloads() == []


def test_with_token_forwards_tenant_header() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["X-Hiveplane-Tenant"] == "other"
        assert request.url.path == "/workloads"
        return httpx.Response(200, json=[])

    assert _client(handler).with_token("t1", tenant_id="other").list_workloads() == []


def test_with_token_omits_tenant_header_when_unset() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert "X-Hiveplane-Tenant" not in request.headers
        return httpx.Response(200, json=[])

    assert _client(handler).with_token("t1").list_workloads() == []


def test_get_queue_hits_queue_path() -> None:
    payload: dict[str, Any] = {"running": [], "pending": []}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/queue"
        return httpx.Response(200, json=payload)

    assert _client(handler).get_queue() == payload


def test_list_health_gets_health_path() -> None:
    payload = [{"workload": "agent-a", "status": "healthy"}]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/health"
        return httpx.Response(200, json=payload)

    assert _client(handler).list_health() == payload


def test_get_roi_hits_fleet_roi_path() -> None:
    payload: dict[str, Any] = {"roi": 1.5}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/cost/roi/fleet"
        return httpx.Response(200, json=payload)

    assert _client(handler).get_roi() == payload


def test_search_passes_query_and_limit() -> None:
    payload = [{"id": "w1"}]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/search"
        assert request.url.params["q"] == "agent"
        assert request.url.params["limit"] == "20"
        return httpx.Response(200, json=payload)

    assert _client(handler).search("agent") == payload


def test_search_passes_custom_limit() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.params["q"] == "agent"
        assert request.url.params["limit"] == "5"
        return httpx.Response(200, json=[])

    assert _client(handler).search("agent", limit=5) == []


def test_list_triggers_and_adapters() -> None:
    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(request.url.path)
        return httpx.Response(200, json=[])

    client = _client(handler)
    assert client.list_triggers() == []
    assert client.list_adapters() == []
    assert calls == ["/triggers", "/adapters"]


def test_get_version_diff_encodes_name_and_params() -> None:
    payload: dict[str, Any] = {"added": [], "removed": []}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.raw_path.decode().startswith("/workloads/agent%20a/versions/diff?")
        assert request.url.params["from_version"] == "1"
        assert request.url.params["to_version"] == "3"
        return httpx.Response(200, json=payload)

    assert _client(handler).get_version_diff("agent a", 1, 3) == payload


def test_compare_certifications_encodes_ids() -> None:
    payload: dict[str, Any] = {"regressions": []}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.raw_path.decode() == "/certifications/compare/b%201/c%202"
        return httpx.Response(200, json=payload)

    assert _client(handler).compare_certifications("b 1", "c 2") == payload


def test_get_run_events_gets_events_path() -> None:
    payload = [{"type": "started"}]

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.url.path == "/runs/r1/events"
        return httpx.Response(200, json=payload)

    assert _client(handler).get_run_events("r1") == payload


def test_add_approval_comment_posts_author_and_text() -> None:
    payload: dict[str, Any] = {"author": "alice", "text": "looks good"}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/approvals/a1/comments"
        assert json.loads(request.content) == {"author": "alice", "text": "looks good"}
        return httpx.Response(201, json=payload)

    assert _client(handler).add_approval_comment("a1", "alice", "looks good") == payload


def test_whoami_gets_auth_whoami_path() -> None:
    payload = {
        "operator_id": "alice",
        "tenant_id": "default",
        "role": "admin",
        "method": "api_key",
        "scopes": [],
    }

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "GET"
        assert request.url.path == "/auth/whoami"
        return httpx.Response(200, json=payload)

    assert _client(handler).whoami() == payload


def test_delegate_approval_posts_assignee_and_operator() -> None:
    payload: dict[str, Any] = {"assignee": "bob"}

    def handler(request: httpx.Request) -> httpx.Response:
        assert request.method == "POST"
        assert request.url.path == "/approvals/a1/delegate"
        assert json.loads(request.content) == {"assignee": "bob", "operator": "alice"}
        return httpx.Response(200, json=payload)

    assert _client(handler).delegate_approval("a1", "bob", "alice") == payload
