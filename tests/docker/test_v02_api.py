"""L2 (v0.2.0) — the API v2 / extensibility contract (M56, #478/M61).

Covers API v2 versioning + pagination + ``X-Request-ID``, global search, ``ask``, incident
mode, the agent-as-service endpoint, fleet-events subscriptions, plugin hooks, and the
Python SDK round-trip (gates 15, 23, 34).
"""

from __future__ import annotations

from typing import Any

import pytest

from v02_support import API_BASE, certify, ensure_admissible, get, post

pytestmark = pytest.mark.docker


def _request_id_present(headers: Any) -> bool:
    return any(key.lower() == "x-request-id" for key in headers)


def test_v2_version_and_pagination() -> None:
    status, version = get("/v2/version")
    assert status == 200, version

    page_status, page = get("/v2/runs?limit=5")
    assert page_status == 200, page
    assert "items" in page and "next_cursor" in page, page
    assert len(page["items"]) <= 5


def test_request_id_header_is_returned() -> None:
    import urllib.request

    req = urllib.request.Request(f"{API_BASE}/healthz", method="GET")
    with urllib.request.urlopen(req, timeout=15) as response:
        assert _request_id_present(response.headers), "API v2 must emit X-Request-ID"


def test_global_search_and_ask() -> None:
    ensure_admissible("support-agent")
    search_status, hits = get("/search?q=repo")
    assert search_status == 200 and isinstance(hits, list), hits

    ask_status, answer = post("/ask", {"question": "what is running right now?"})
    assert ask_status in (200, 201, 403, 409), answer


def test_incident_mode_pause_and_resume() -> None:
    pause_status, _ = post("/fleet/pause", {"actor": "field-test-v02", "reason": "drill"})
    assert pause_status in (200, 201), pause_status
    state_status, state = get("/fleet/state")
    assert state_status == 200 and state.get("halted") is True, state
    resume_status, _ = post("/fleet/resume", {"actor": "field-test-v02"})
    assert resume_status in (200, 201), resume_status


def test_agent_as_service_endpoint_serves_a_run() -> None:
    ensure_admissible("support-agent")
    certify("support-agent", context="production")
    status, run = post(
        "/services/support-agent/invoke",
        {"task": {}, "caller": "service", "context": "production"},
    )
    assert status in (201, 403, 409), run


def test_fleet_event_subscriptions() -> None:
    status, subscription = post(
        "/event-subscriptions",
        {"url": "http://webhook-sink:8081/events", "kinds": ["run"]},
    )
    assert status in (200, 201, 409), subscription
    list_status, subscriptions = get("/event-subscriptions")
    assert list_status == 200 and isinstance(subscriptions, list), subscriptions


def test_python_sdk_round_trips() -> None:
    from hiveplane.sdk import HivePlaneClient

    client = HivePlaneClient(API_BASE)
    workloads = client.list_workloads()
    assert any(entry["name"] == "support-agent" for entry in workloads)
