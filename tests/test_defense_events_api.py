"""Security-events API (M39-06)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
from hiveplane.defense.events import SecurityEvent, SecurityEventKind


def _client() -> TestClient:
    return TestClient(create_app())


def _seed(client: TestClient, event: SecurityEvent) -> None:
    client.app.state.security_event_store.add(event)  # type: ignore[attr-defined]


def test_lists_security_events() -> None:
    client = _client()
    _seed(
        client,
        SecurityEvent(
            event_id="e1",
            run_id="run-1",
            workload_id="repo-agent",
            kind=SecurityEventKind.INJECTION,
            detector_id="injection.instruction_override",
            detector_version="1.0.0",
            detail={"rule": "injection.scan"},
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )
    response = client.get("/security/events")
    assert response.status_code == 200
    body = response.json()
    assert body[0]["event_id"] == "e1"
    assert body[0]["kind"] == "injection"


def test_filters_security_events_by_workload_and_kind() -> None:
    client = _client()
    _seed(
        client,
        SecurityEvent(
            event_id="e1",
            workload_id="repo-agent",
            kind=SecurityEventKind.INJECTION,
            created_at=datetime(2026, 1, 1, tzinfo=UTC),
        ),
    )
    _seed(
        client,
        SecurityEvent(
            event_id="e2",
            workload_id="triage",
            kind=SecurityEventKind.EGRESS_DENIED,
            created_at=datetime(2026, 1, 2, tzinfo=UTC),
        ),
    )
    assert [e["event_id"] for e in client.get("/security/events?workload_id=triage").json()] == [
        "e2"
    ]
    assert [
        e["event_id"]
        for e in client.get("/security/events?kind=egress_denied").json()
    ] == ["e2"]
    assert client.get("/security/events?run_id=missing").json() == []
