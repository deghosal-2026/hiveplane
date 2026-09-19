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


def test_transport_error_maps_to_control_plane_error() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        raise httpx.ConnectError("connection refused")

    with pytest.raises(ControlPlaneError) as excinfo:
        _client(handler).list_workloads()

    assert excinfo.value.status_code is None
    assert "connection refused" in excinfo.value.detail
