"""Agent-tool API and store tests (M30-04..M30-06, #202-#204)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from hiveplane.agent_tools.engine import AgentToolInvoker
from hiveplane.agent_tools.models import AgentToolInvocation, InvocationDecision
from hiveplane.agent_tools.registry import AgentToolRegistry
from hiveplane.agent_tools.store import (
    AgentToolStore,
    InMemoryAgentToolStore,
    PostgresAgentToolStore,
)
from hiveplane.api.app import create_app
from hiveplane.core.run import AdmissionContext
from postgres import reset_database
from test_agent_tools import FakeRegistry, FakeRunService

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _invocation() -> AgentToolInvocation:
    return AgentToolInvocation(
        invocation_id="atool-1",
        caller_run_id="run-caller",
        nested_run_id="run-nested",
        tool_id="agent.triage",
        workload="triage",
        depth=1,
        chain=["triage"],
        context=AdmissionContext.STAGING,
        decision=InvocationDecision.ALLOWED,
        cost_usd=0.25,
        created_at=_NOW,
    )


def _assert_store(store: AgentToolStore) -> None:
    store.save_invocation(_invocation())
    loaded = store.get_invocation("atool-1")
    assert loaded is not None and loaded.workload == "triage"
    assert store.get_invocation("missing") is None
    assert [i.invocation_id for i in store.list_invocations()] == ["atool-1"]
    assert [
        i.invocation_id
        for i in store.list_invocations(caller_run_id="run-caller")
    ] == ["atool-1"]
    assert store.list_invocations(caller_run_id="other") == []

    refused = _invocation().model_copy(
        update={"decision": InvocationDecision.REFUSED, "nested_run_id": None}
    )
    store.save_invocation(refused)
    updated = store.get_invocation("atool-1")
    assert updated is not None and updated.decision is InvocationDecision.REFUSED

    store.clear()
    assert store.list_invocations() == []


def test_in_memory_agent_tool_store() -> None:
    _assert_store(InMemoryAgentToolStore())


def test_postgres_agent_tool_store(pg_engine: Engine) -> None:
    reset_database(pg_engine)
    _assert_store(PostgresAgentToolStore(pg_engine))


def _client(admitted: set[str] | None = None) -> TestClient:
    app = create_app()
    registry = AgentToolRegistry(
        FakeRegistry({"triage": "triage agent", "remediate": "fix agent"}, admitted)
    )
    app.state.agent_tool_registry = registry
    app.state.agent_tool_invoker = AgentToolInvoker(registry, FakeRunService())
    return TestClient(app)


def test_agent_tools_list() -> None:
    client = _client(admitted={"triage"})
    tools = client.get("/agent-tools").json()
    assert [tool["tool_id"] for tool in tools] == ["agent.triage"]
    assert tools[0]["description"] == "triage agent"


def test_agent_tool_invoke_allowed() -> None:
    client = _client()
    response = client.post(
        "/agent-tools/agent.triage/invoke",
        json={"caller_run_id": "run-caller", "task": {"x": 1}},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["decision"] == "allowed"
    assert body["nested_run_id"] == "run-nested"
    assert body["chain"] == ["triage"]


def test_agent_tool_invoke_uncertified_is_forbidden() -> None:
    client = _client(admitted={"triage"})
    response = client.post(
        "/agent-tools/agent.remediate/invoke",
        json={"caller_run_id": "run-caller"},
    )
    assert response.status_code == 403
    assert "not certified" in response.json()["detail"]


def test_agent_tools_invalid_context() -> None:
    client = _client()
    assert client.get("/agent-tools?context=bogus").status_code == 422
