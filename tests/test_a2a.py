"""A2A interop adapter tests (M30-07, #205, stretch)."""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from fastapi import HTTPException
from fastapi.testclient import TestClient

from hiveplane.a2a import A2AAdapter, A2ATaskRequest
from hiveplane.agent_tools.registry import AgentToolRegistry
from hiveplane.api.app import create_app
from hiveplane.api.deps import get_a2a_adapter
from hiveplane.config import get_settings
from hiveplane.core.run import AdmissionContext
from hiveplane.router.catalog import StaticCatalog
from hiveplane.router.engine import RouterEngine
from test_agent_tools import FakeRegistry, FakeRunService
from test_router_api import KeywordClassifier


def _adapter(*, with_router: bool = True, allowed: list[str] | None = None) -> A2AAdapter:
    registry = AgentToolRegistry(FakeRegistry({"triage": "triage agent"}))
    router = (
        RouterEngine(
            StaticCatalog(["triage"]),
            KeywordClassifier({"triage": ["database", "down"]}),
        )
        if with_router
        else None
    )
    return A2AAdapter(
        registry,
        FakeRunService(),
        router=router,
        allowed_planes=allowed,
    )


def test_agent_cards_expose_certified_workloads() -> None:
    cards = _adapter().agent_cards("http://plane.local")
    assert [card.name for card in cards] == ["triage"]
    assert cards[0].url == "http://plane.local/a2a/agents/triage"
    assert cards[0].skills[0].id == "agent.triage"


def test_handle_task_routes_and_submits() -> None:
    result = _adapter().handle_task(
        A2ATaskRequest(task_id="t1", message="the database is down")
    )
    assert result.state == "submitted"
    assert result.target == "triage"
    assert result.run_id == "run-nested"


def test_handle_task_refuses_low_confidence() -> None:
    result = _adapter().handle_task(A2ATaskRequest(task_id="t1", message="hello"))
    assert result.state == "refused"
    assert result.reason == "low_confidence"


def test_handle_task_refuses_without_router() -> None:
    result = _adapter(with_router=False).handle_task(
        A2ATaskRequest(task_id="t1", message="the database is down")
    )
    assert result.state == "refused"
    assert result.reason == "router is not enabled"


def test_untrusted_plane_is_refused() -> None:
    adapter = _adapter(allowed=["friend"])
    assert adapter.plane_id == "hiveplane"
    assert adapter.trusts("friend") is True
    assert adapter.trusts("stranger") is False
    result = adapter.handle_task(
        A2ATaskRequest(
            task_id="t1",
            message="the database is down",
            context=AdmissionContext.STAGING,
            plane_id="stranger",
        )
    )
    assert result.state == "refused"
    assert "not registered" in (result.reason or "")


def test_handle_task_refuses_when_admission_fails() -> None:
    registry = AgentToolRegistry(FakeRegistry({"triage": "triage agent"}))
    router = RouterEngine(
        StaticCatalog(["triage"]),
        KeywordClassifier({"triage": ["database", "down"]}),
    )
    adapter = A2AAdapter(registry, FakeRunService(refuse=True), router=router)
    result = adapter.handle_task(A2ATaskRequest(task_id="t1", message="database down"))
    assert result.state == "refused"
    assert result.target == "triage"
    assert "refused admission" in (result.reason or "")


def test_a2a_api_disabled_is_not_mounted() -> None:
    client = TestClient(create_app())
    assert client.get("/a2a/agents").status_code == 404
    assert client.post("/a2a/tasks", json={"task_id": "t", "message": "m"}).status_code == 404


def test_get_a2a_adapter_returns_503_without_adapter() -> None:
    request = SimpleNamespace(app=SimpleNamespace(state=SimpleNamespace()))
    with pytest.raises(HTTPException) as excinfo:
        get_a2a_adapter(request)  # type: ignore[arg-type]
    assert excinfo.value.status_code == 503


def test_a2a_api_enabled_when_flagged(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("HIVEPLANE_A2A__ENABLED", "true")
    get_settings.cache_clear()
    try:
        app = create_app()
        app.state.a2a_adapter = _adapter()
        client = TestClient(app)
        assert client.get("/a2a/agents").json()[0]["name"] == "triage"
        result = client.post(
            "/a2a/tasks", json={"task_id": "t1", "message": "the database is down"}
        ).json()
        assert result["state"] == "submitted"
    finally:
        get_settings.cache_clear()
