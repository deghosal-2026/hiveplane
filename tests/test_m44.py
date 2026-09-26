"""Tests for MCP Registry v2 live transport (M44)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from types import SimpleNamespace

import pytest

from hiveplane.core.event import EventType
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.tools import ToolTrustLevel
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.models import InterventionAction
from hiveplane.execution.tool_executor import ToolExecutionError
from hiveplane.execution.tools import ToolCallOutcome, ToolCallRequest, ToolGateway
from hiveplane.mcp.models import (
    McpErrorCode,
    McpServerEndpoint,
    McpServerStatus,
    McpToolDefinition,
    McpTransportKind,
    ToolStatus,
)
from hiveplane.mcp.registry import McpRegistry, UnknownToolError
from hiveplane.mcp.transport import (
    FixtureMcpTransport,
    FixtureMcpTransportFactory,
    McpTransportError,
)
from hiveplane.policy.engine import PolicyEngine
from hiveplane.policy.kill_switch import InMemoryKillSwitchStore, KillSwitch
from hiveplane.policy.packs import InMemoryPolicyPackStore

_FIXED_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _endpoint(*, target: str = "python") -> McpServerEndpoint:
    return McpServerEndpoint(
        kind=McpTransportKind.STDIO, target=target, args=["-m", "fixture"]
    )


def _defs(*names: str) -> list[McpToolDefinition]:
    return [McpToolDefinition(name=name, description=f"{name} tool") for name in names]


def _registry(
    *, transport: FixtureMcpTransport | None = None
) -> tuple[McpRegistry, FixtureMcpTransport]:
    transport = transport or FixtureMcpTransport(
        tools=_defs("read_issue", "restart_service"),
        outputs={
            "read_issue": '{"title": "bug"}',
            "restart_service": '{"ok": true}',
        },
    )
    factory = FixtureMcpTransportFactory({_endpoint().fingerprint: transport})
    return McpRegistry(factory, clock=lambda: _FIXED_NOW), transport


# --------------------------------------------------------------------------- #
# M44-01/02 — transport, connect, discovery
# --------------------------------------------------------------------------- #
def test_connect_discovers_tools_but_they_are_not_callable() -> None:
    registry, _ = _registry()

    server = registry.connect(_endpoint())

    assert server.status is McpServerStatus.CONNECTED
    tools = registry.list_tools()
    assert {tool.tool_name for tool in tools} == {"read_issue", "restart_service"}
    assert all(tool.status is ToolStatus.DISCOVERED for tool in tools)
    assert all(not registry.is_available(tool.tool_id) for tool in tools)


def test_onboard_activates_and_call_returns_real_output() -> None:
    registry, _ = _registry()
    registry.connect(_endpoint())
    discovered = next(t for t in registry.list_tools() if t.tool_name == "read_issue")

    onboarded = registry.onboard(
        discovered.tool_id, trust_level=ToolTrustLevel.READ_ONLY, actor="alice"
    )
    result = registry.call(onboarded.tool_id)

    assert onboarded.status is ToolStatus.ACTIVE
    assert result.output == '{"title": "bug"}'


def test_stable_ids_survive_server_restart() -> None:
    registry, transport = _registry()
    registry.connect(_endpoint())
    original = {tool.tool_name: tool.tool_id for tool in registry.list_tools()}

    transport.reset_connection()
    registry.refresh(registry.list_servers()[0].server_id)

    after = {tool.tool_name: tool.tool_id for tool in registry.list_tools()}
    assert after == original


def test_renamed_tool_gets_new_id_and_old_id_retired() -> None:
    registry, transport = _registry()
    registry.connect(_endpoint())
    old_id = next(t.tool_id for t in registry.list_tools() if t.tool_name == "read_issue")

    transport.set_tools(_defs("read_issue_v2", "restart_service"))
    registry.refresh(registry.list_servers()[0].server_id)

    by_name = {tool.tool_name: tool for tool in registry.list_tools()}
    assert by_name["read_issue"].status is ToolStatus.ABSENT
    assert by_name["read_issue"].tool_id == old_id
    assert by_name["read_issue_v2"].tool_id != old_id
    assert not registry.is_available(old_id)


def test_removed_tool_is_marked_absent_and_unavailable() -> None:
    registry, transport = _registry()
    registry.connect(_endpoint())
    tool = next(t for t in registry.list_tools() if t.tool_name == "read_issue")
    registry.onboard(tool.tool_id, trust_level=ToolTrustLevel.READ_ONLY, actor="alice")

    transport.set_tools(_defs("restart_service"))
    registry.refresh(registry.list_servers()[0].server_id)

    assert registry.get_tool(tool.tool_id).status is ToolStatus.ABSENT
    assert not registry.is_available(tool.tool_id)


def test_unknown_tool_raises() -> None:
    registry, _ = _registry()
    with pytest.raises(UnknownToolError):
        registry.get_tool("tool-missing")


def test_transport_errors_are_normalized() -> None:
    registry, transport = _registry()
    registry.connect(_endpoint())
    tool = registry.list_tools()[0]
    registry.onboard(tool.tool_id, trust_level=ToolTrustLevel.READ_ONLY, actor="alice")

    transport.fail_next(McpErrorCode.TIMEOUT, "deadline exceeded")
    with pytest.raises(McpTransportError) as excinfo:
        registry.call(tool.tool_id)
    assert excinfo.value.code is McpErrorCode.TIMEOUT


def test_onboarding_records_trust_level_and_versions() -> None:
    registry, _ = _registry()
    registry.connect(_endpoint())
    tool = next(t for t in registry.list_tools() if t.tool_name == "restart_service")

    onboarded = registry.onboard(
        tool.tool_id, trust_level=ToolTrustLevel.DESTRUCTIVE, actor="alice"
    )

    assert onboarded.trust_level is ToolTrustLevel.DESTRUCTIVE
    assert onboarded.current_version == 1
    assert registry.versions(tool.tool_id)[0].version == 1


# --------------------------------------------------------------------------- #
# M44-05/06 — manifest allow-list + execution through the boundary
# --------------------------------------------------------------------------- #
class _Runs:
    def __init__(self, run: Run) -> None:
        self._run = run
        self.events: list[EventType] = []

    def get(self, run_id: str) -> Run:
        return self._run

    def intervene(self, run_id: str, action: InterventionAction, *, actor: str) -> Run:
        return self._run

    def record_event(
        self, run_id: str, event_type: EventType, actor: str, *, detail: str | None = None
    ) -> None:
        self.events.append(event_type)


def _run(*, read_only: bool = False) -> Run:
    return Run(
        id="run-1",
        workload_id="agent-1",
        caller="cli",
        state=RunState.RUNNING,
        model_identity="openai/gpt-4o/2024-08-06",
        created_at=_FIXED_NOW,
        updated_at=_FIXED_NOW,
        context=AdmissionContext.STAGING,
        read_only=read_only,
    )


def _gateway(
    make_manifest: Callable[..., AgentWorkload],
    *,
    tools: dict[str, object],
    executor: object | None = None,
    kill_switch: KillSwitch | None = None,
) -> tuple[ToolGateway, _Runs]:
    workload = make_manifest(name="agent-1", status="provisional", tools=tools)
    runs = _Runs(_run())
    gateway = ToolGateway(
        SimpleNamespace(get=lambda name: SimpleNamespace(manifest=workload)),  # type: ignore[arg-type]
        PolicyEngine(InMemoryPolicyPackStore(), clock=lambda: _FIXED_NOW),
        runs,
        executor=executor,  # type: ignore[arg-type]
        kill_switch=kill_switch,
        clock=lambda: _FIXED_NOW,
    )
    return gateway, runs


def test_tool_outside_allow_list_is_denied(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    gateway, _ = _gateway(
        make_manifest,
        tools={"allow": [{"tool_id": "tool-read", "trust_level": "read_only"}]},
    )

    result = gateway.invoke("run-1", ToolCallRequest(tool_id="tool-other"))

    assert result.outcome is ToolCallOutcome.DENIED
    assert result.rule == "tool_not_allowed"


def test_live_transport_executes_through_the_boundary(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    from hiveplane.mcp.executor import McpToolExecutor

    registry, _ = _registry()
    registry.connect(_endpoint())
    tool = next(t for t in registry.list_tools() if t.tool_name == "restart_service")
    registry.onboard(tool.tool_id, trust_level=ToolTrustLevel.READ_ONLY, actor="alice")
    gateway, _ = _gateway(
        make_manifest,
        tools={"allow": [{"tool_id": tool.tool_id, "trust_level": "read_only"}]},
        executor=McpToolExecutor(registry),
    )

    result = gateway.invoke("run-1", ToolCallRequest(tool_id=tool.tool_id))

    assert result.outcome is ToolCallOutcome.ALLOWED
    assert registry.call(tool.tool_id).output == '{"ok": true}'


def test_executor_surfaces_transport_error_as_tool_execution_error() -> None:
    from hiveplane.mcp.executor import McpToolExecutor

    registry, transport = _registry()
    registry.connect(_endpoint())
    tool = registry.list_tools()[0]
    registry.onboard(tool.tool_id, trust_level=ToolTrustLevel.READ_ONLY, actor="alice")
    transport.fail_next(McpErrorCode.UNREACHABLE, "server down")

    with pytest.raises(ToolExecutionError):
        McpToolExecutor(registry).execute(tool.tool_id)


def test_kill_switch_disables_a_discovered_tool(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    kill_switch = KillSwitch(InMemoryKillSwitchStore(), clock=lambda: _FIXED_NOW)
    gateway, _ = _gateway(
        make_manifest,
        tools={"allow": [{"tool_id": "tool-read", "trust_level": "read_only"}]},
        kill_switch=kill_switch,
    )
    kill_switch.disable("tool-read", reason="incident", actor="oncall")

    result = gateway.invoke("run-1", ToolCallRequest(tool_id="tool-read"))

    assert result.outcome is ToolCallOutcome.DENIED
    assert result.rule == "kill_switch"


def test_refresh_does_not_resurrect_killed_tool() -> None:
    registry, _ = _registry()
    registry.connect(_endpoint())
    tool = registry.list_tools()[0]
    registry.onboard(tool.tool_id, trust_level=ToolTrustLevel.READ_ONLY, actor="alice")

    kill_switch = KillSwitch(InMemoryKillSwitchStore(), clock=lambda: _FIXED_NOW)
    kill_switch.disable(tool.tool_id, reason="incident", actor="oncall")
    registry.refresh(registry.list_servers()[0].server_id)

    assert kill_switch.is_disabled(tool.tool_id)


def _plain(text: str) -> str:
    return text


def test_registry_persists_stable_ids_across_reload() -> None:
    from hiveplane.mcp.store import InMemoryMcpStore

    store = InMemoryMcpStore()
    transport = FixtureMcpTransport(
        tools=_defs("read_issue", "restart_service"),
        outputs={"read_issue": '{"title": "bug"}'},
    )
    factory = FixtureMcpTransportFactory({_endpoint().fingerprint: transport})
    first = McpRegistry(factory, clock=lambda: _FIXED_NOW, store=store)
    first.connect(_endpoint())
    tool = next(t for t in first.list_tools() if t.tool_name == "read_issue")
    first.onboard(tool.tool_id, trust_level=ToolTrustLevel.READ_ONLY, actor="alice")

    second = McpRegistry(factory, clock=lambda: _FIXED_NOW, store=store)
    second.load_from_store()

    reloaded = second.get_tool(tool.tool_id)
    assert reloaded.status is ToolStatus.ACTIVE
    assert reloaded.trust_level is ToolTrustLevel.READ_ONLY
    assert second.versions(tool.tool_id)[0].version == 1
    assert second.list_servers()[0].server_id == first.list_servers()[0].server_id


def test_live_stdio_transport_discovers_and_calls_real_tools() -> None:
    import sys
    from pathlib import Path

    from hiveplane.mcp.transport import DefaultMcpTransportFactory

    script = Path(__file__).resolve().parents[1] / "deploy/testdata/mcp/fixture_server.py"
    endpoint = McpServerEndpoint(
        kind=McpTransportKind.STDIO, target=sys.executable, args=[str(script)]
    )
    registry = McpRegistry(DefaultMcpTransportFactory())
    try:
        registry.connect(endpoint)
        tools = {tool.tool_name: tool for tool in registry.list_tools()}
        assert {"read_file", "delete_file"} <= set(tools)

        registry.onboard(
            tools["read_file"].tool_id, trust_level=ToolTrustLevel.READ_ONLY, actor="a"
        )
        result = registry.call(tools["read_file"].tool_id, {"path": "/tmp/x"})
        assert result.output == "contents of /tmp/x"
    finally:
        registry.close()


def test_mcp_api_connect_discover_onboard_remove() -> None:
    import sys
    from pathlib import Path

    from fastapi.testclient import TestClient

    from hiveplane.api.app import create_app

    script = Path(__file__).resolve().parents[1] / "deploy/testdata/mcp/fixture_server.py"
    endpoint = {"kind": "stdio", "target": sys.executable, "args": [str(script)]}
    client = TestClient(create_app())
    client.__enter__()

    created = client.post("/mcp/servers", json={"endpoint": endpoint})
    assert created.status_code == 201
    server = created.json()
    assert client.get("/mcp/servers").status_code == 200
    assert client.delete("/mcp/tools/tool-missing").status_code == 404

    tools = client.get("/mcp/tools").json()
    names = {tool["tool_name"] for tool in tools}
    assert {"read_file", "delete_file"} <= names
    tool_id = next(t["tool_id"] for t in tools if t["tool_name"] == "read_file")

    assert client.get(f"/mcp/tools/{tool_id}").json()["tool_id"] == tool_id
    onboarded = client.post(
        f"/mcp/tools/{tool_id}/onboard", json={"trust_level": "read_only", "actor": "alice"}
    )
    assert onboarded.json()["status"] == "active"
    active = client.get("/mcp/tools", params={"status": "active"}).json()
    assert {t["tool_id"] for t in active} >= {tool_id}

    refreshed = client.post(f"/mcp/servers/{server['server_id']}/refresh")
    assert refreshed.status_code == 200
    retired = client.delete(f"/mcp/tools/{tool_id}")
    assert retired.json()["status"] == "retired"

    assert client.post("/mcp/servers/missing/refresh").status_code == 404
    assert client.get("/mcp/tools/tool-missing").status_code == 404
    rejected = client.post(
        f"/mcp/tools/{tool_id}/onboard", json={"trust_level": "read_only", "actor": "a"}
    )
    assert rejected.status_code == 409
    missing = client.post(
        "/mcp/tools/tool-missing/onboard",
        json={"trust_level": "read_only", "actor": "a"},
    )
    assert missing.status_code == 404
    unavailable = client.post(
        "/mcp/servers", json={"endpoint": {"kind": "http", "target": "http://127.0.0.1:1"}}
    )
    assert unavailable.status_code == 502
    client.__exit__(None, None, None)


def test_registry_error_paths() -> None:
    from hiveplane.mcp.registry import ToolNotAvailableError, UnknownServerError

    registry, _ = _registry()
    registry.connect(_endpoint())
    discovered = registry.list_tools()[0]
    with pytest.raises(ToolNotAvailableError):
        registry.call(discovered.tool_id)

    registry.remove(discovered.tool_id)
    with pytest.raises(ToolNotAvailableError):
        registry.onboard(discovered.tool_id, trust_level=ToolTrustLevel.READ_ONLY, actor="a")
    with pytest.raises(UnknownServerError):
        registry.refresh("srv-nope")
    with pytest.raises(UnknownToolError):
        registry.versions("tool-nope")


def test_list_tools_filters() -> None:
    registry, _ = _registry()
    registry.connect(_endpoint())
    tool = next(t for t in registry.list_tools() if t.tool_name == "read_issue")
    registry.onboard(tool.tool_id, trust_level=ToolTrustLevel.DESTRUCTIVE, actor="a")

    assert len(registry.list_tools(status=ToolStatus.ACTIVE)) == 1
    assert len(registry.list_tools(trust_level=ToolTrustLevel.DESTRUCTIVE)) == 1
    assert len(registry.list_tools(server_id="srv-nope")) == 0


def test_transport_rejects_wrong_kind_endpoints() -> None:
    from hiveplane.mcp.transport import (
        DefaultMcpTransportFactory,
        McpHttpTransport,
        McpStdioTransport,
    )

    with pytest.raises(McpTransportError):
        McpStdioTransport(McpServerEndpoint(kind=McpTransportKind.HTTP, target="http://x"))
    with pytest.raises(McpTransportError):
        McpHttpTransport(_endpoint())
    with pytest.raises(McpTransportError):
        DefaultMcpTransportFactory().create(
            McpServerEndpoint(kind=McpTransportKind.SSE, target="http://x")
        )


def test_live_http_transport_discovers_and_calls() -> None:
    import http.server
    import json
    import threading

    from hiveplane.mcp.transport import McpHttpTransport

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            length = int(self.headers["Content-Length"])
            body = json.loads(self.rfile.read(length))
            method = body["method"]
            if method == "initialize":
                result: dict[str, object] = {"protocolVersion": "1", "capabilities": {}}
            elif method == "tools/list":
                result = {"tools": [{"name": "ping", "inputSchema": {}}]}
            elif body.get("params", {}).get("name") == "boom":
                payload = json.dumps(
                    {"jsonrpc": "2.0", "id": body["id"], "error": {"code": -1, "message": "no"}}
                ).encode()
                self.send_response(200)
                self.send_header("Content-Length", str(len(payload)))
                self.end_headers()
                self.wfile.write(payload)
                return
            else:
                result = {"content": [{"type": "text", "text": "pong"}]}
            payload = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: object) -> None:
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = McpServerEndpoint(
            kind=McpTransportKind.HTTP, target=f"http://127.0.0.1:{server.server_port}"
        )
        transport = McpHttpTransport(endpoint)
        assert transport.list_tools()[0].name == "ping"
        assert transport.call_tool("ping") == "pong"
        with pytest.raises(McpTransportError):
            transport.call_tool("boom")
    finally:
        server.shutdown()


def test_composite_executor_falls_through_and_raises() -> None:
    from hiveplane.execution.tool_executor import (
        CompositeToolExecutor,
        FixtureToolExecutor,
        ToolExecutionError,
    )

    class _Static:
        def execute(self, tool_id: str) -> str:
            return "served"

    composite = CompositeToolExecutor([FixtureToolExecutor("/nonexistent"), _Static()])
    assert composite.execute("tool-x") == "served"

    with pytest.raises(ToolExecutionError):
        CompositeToolExecutor([FixtureToolExecutor("/nonexistent")]).execute("tool-x")
    with pytest.raises(ToolExecutionError):
        CompositeToolExecutor([]).execute("tool-x")


def test_postgres_mcp_store_round_trip(pg_engine: object) -> None:
    from sqlalchemy import Engine

    from hiveplane.mcp.models import McpServerRecord, McpToolRecord, ToolVersion
    from hiveplane.mcp.store import PostgresMcpStore
    from postgres import ensure_schema

    engine = pg_engine
    assert isinstance(engine, Engine)
    ensure_schema(engine)
    endpoint = _endpoint()
    server = McpServerRecord(
        server_id="srv-1",
        endpoint=endpoint,
        fingerprint=endpoint.fingerprint,
        status=McpServerStatus.CONNECTED,
        connected_at=_FIXED_NOW,
        last_seen=_FIXED_NOW,
    )
    tool = McpToolRecord(
        tool_id="tool-1",
        server_id="srv-1",
        server_fingerprint=endpoint.fingerprint,
        tool_name="read_file",
        trust_level=ToolTrustLevel.READ_ONLY,
        status=ToolStatus.ACTIVE,
        discovered_at=_FIXED_NOW,
    )
    version = ToolVersion(tool_id="tool-1", version=1, registered_at=_FIXED_NOW)

    store = PostgresMcpStore(engine)
    store.clear()
    store.save_server(server)
    store.save_tool(tool)
    store.save_version(version)
    store.save_version(version)

    reopened = PostgresMcpStore(engine)
    assert reopened.list_servers()[0].server_id == "srv-1"
    assert reopened.get_tool("tool-1") is not None
    assert reopened.list_tools()[0].tool_name == "read_file"
    assert reopened.list_versions("tool-1")[0].version == 1
    assert reopened.get_tool("missing") is None

    store.clear()
    assert reopened.get_tool("tool-1") is None
    assert reopened.list_servers() == []
    assert reopened.list_versions("tool-1") == []


def test_in_memory_store_round_trip_and_clear() -> None:
    from hiveplane.mcp.models import McpServerRecord, McpToolRecord, ToolVersion
    from hiveplane.mcp.store import InMemoryMcpStore

    endpoint = _endpoint()
    store = InMemoryMcpStore()
    server = McpServerRecord(
        server_id="srv-1",
        endpoint=endpoint,
        fingerprint=endpoint.fingerprint,
        status=McpServerStatus.CONNECTED,
        connected_at=_FIXED_NOW,
        last_seen=_FIXED_NOW,
    )
    tool = McpToolRecord(
        tool_id="tool-1",
        server_id="srv-1",
        server_fingerprint=endpoint.fingerprint,
        tool_name="read_file",
        status=ToolStatus.ACTIVE,
        discovered_at=_FIXED_NOW,
    )
    store.save_server(server)
    store.save_tool(tool)
    store.save_version(ToolVersion(tool_id="tool-1", version=1, registered_at=_FIXED_NOW))

    assert store.list_servers()[0].server_id == "srv-1"
    assert store.get_tool("tool-1") is not None
    assert store.get_tool("missing") is None
    assert store.list_tools()[0].tool_id == "tool-1"
    assert store.list_versions("tool-1")[0].version == 1

    store.clear()
    assert store.list_servers() == []
    assert store.get_tool("tool-1") is None
    assert store.list_versions("tool-1") == []


def test_registry_load_without_store_is_noop() -> None:
    registry, _ = _registry()
    registry.load_from_store()
    assert registry.list_servers() == []


def test_registry_reconnect_preserves_identity_and_close() -> None:
    registry, _ = _registry()
    first = registry.connect(_endpoint())
    first_tools = {tool.tool_name: tool.tool_id for tool in registry.list_tools()}
    second = registry.connect(_endpoint())

    assert second.connected_at == first.connected_at
    assert {tool.tool_name: tool.tool_id for tool in registry.list_tools()} == first_tools
    registry.close()


def test_registry_schema_drift_creates_new_version() -> None:
    registry, transport = _registry()
    registry.connect(_endpoint())
    tool = next(t for t in registry.list_tools() if t.tool_name == "read_issue")
    registry.onboard(tool.tool_id, trust_level=ToolTrustLevel.READ_ONLY, actor="a")

    transport.set_tools(
        [
            McpToolDefinition(name="read_issue", input_schema={"type": "object"}),
            McpToolDefinition(name="restart_service"),
        ]
    )
    registry.refresh(registry.list_servers()[0].server_id)

    assert [v.version for v in registry.versions(tool.tool_id)] == [1, 2]
    assert registry.get_tool(tool.tool_id).current_version == 2


def test_endpoint_helpers() -> None:
    from hiveplane.mcp.registry import http_endpoint, sse_endpoint, stdio_endpoint

    assert stdio_endpoint("python", "-m", "x").args == ["-m", "x"]
    assert http_endpoint("http://x").kind is McpTransportKind.HTTP
    assert sse_endpoint("v").kind is McpTransportKind.SSE


def test_fixture_transport_edges() -> None:
    transport = FixtureMcpTransport(tools=_defs("a"), outputs={})
    transport.set_output("a", "value")
    assert transport.call_tool("a") == "value"

    transport._connected = False
    with pytest.raises(McpTransportError):
        transport.list_tools()
    with pytest.raises(McpTransportError):
        transport.call_tool("a")
    transport._connected = True

    factory = FixtureMcpTransportFactory({})
    with pytest.raises(McpTransportError):
        factory.create(_endpoint())


def test_stdio_transport_failures() -> None:
    from hiveplane.mcp.transport import McpStdioTransport

    with pytest.raises(McpTransportError):
        McpStdioTransport(
            McpServerEndpoint(kind=McpTransportKind.STDIO, target="/nonexistent/mcp-binary")
        )

    with pytest.raises(McpTransportError) as timeout_exc:
        McpStdioTransport(
            McpServerEndpoint(
                kind=McpTransportKind.STDIO, target="/bin/sh", args=["-c", "sleep 5"]
            ),
            timeout=0.2,
        )
    assert timeout_exc.value.code is McpErrorCode.TIMEOUT

    dead = McpStdioTransport(
        McpServerEndpoint(kind=McpTransportKind.STDIO, target="/bin/cat")
    )
    dead.close()
    dead.close()
    with pytest.raises(McpTransportError):
        dead.list_tools()


def test_stdio_transport_error_response() -> None:
    import sys
    from pathlib import Path

    from hiveplane.mcp.transport import McpStdioTransport

    script = Path(__file__).resolve().parents[1] / "deploy/testdata/mcp/fixture_server.py"
    transport = McpStdioTransport(
        McpServerEndpoint(kind=McpTransportKind.STDIO, target=sys.executable, args=[str(script)])
    )
    try:
        with pytest.raises(McpTransportError) as excinfo:
            transport.call_tool("does_not_exist")
        assert excinfo.value.code is McpErrorCode.ERROR
    finally:
        transport.close()


def test_http_transport_text_content_fallbacks() -> None:
    import http.server
    import json
    import threading

    from hiveplane.mcp.transport import McpHttpTransport

    class Handler(http.server.BaseHTTPRequestHandler):
        def do_POST(self) -> None:
            body = json.loads(self.rfile.read(int(self.headers["Content-Length"])))
            name = body.get("params", {}).get("name")
            if name == "httpfail":
                self.send_error(500)
                return
            if name == "structured":
                result: object = {"structuredContent": {"a": 1}}
            elif body["method"] == "tools/list":
                result = {"tools": [{"name": "x"}]}
            else:
                result = {}
            payload = json.dumps({"jsonrpc": "2.0", "id": body["id"], "result": result}).encode()
            self.send_response(200)
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *args: object) -> None:
            return

    server = http.server.HTTPServer(("127.0.0.1", 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        endpoint = McpServerEndpoint(
            kind=McpTransportKind.HTTP, target=f"http://127.0.0.1:{server.server_port}"
        )
        transport = McpHttpTransport(endpoint)
        assert transport.call_tool("structured") == '{"a": 1}'
        assert transport.call_tool("raw") == "{}"
        with pytest.raises(McpTransportError) as excinfo:
            transport.call_tool("httpfail")
        assert excinfo.value.code is McpErrorCode.ERROR
    finally:
        server.shutdown()


def test_stdio_transport_non_dict_and_closed_stream() -> None:
    import sys

    from hiveplane.mcp.transport import McpStdioTransport

    echoing = McpStdioTransport(
        McpServerEndpoint(
            kind=McpTransportKind.STDIO,
            target=sys.executable,
            args=["-c", "import sys\nfor line in sys.stdin:\n    print('[1]', flush=True)"],
        )
    )
    try:
        assert echoing.list_tools() == []
    finally:
        echoing.close()

    with pytest.raises(McpTransportError) as closed:
        McpStdioTransport(
            McpServerEndpoint(
                kind=McpTransportKind.STDIO,
                target=sys.executable,
                args=["-c", "import sys; sys.stdin.readline()"],
            )
        )
    assert closed.value.code is McpErrorCode.UNREACHABLE
