"""Tests that the live run path emits correlated OpenTelemetry spans (M19, #48)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from hiveplane.certification.models import TargetContext
from hiveplane.core.approval import ApprovalStatus
from hiveplane.core.decision import PolicyContext
from hiveplane.core.event import EventType
from hiveplane.core.fanout import FanOutType
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.usage import UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.fanout import FanOutService
from hiveplane.execution.models import InterventionAction
from hiveplane.execution.store import InMemoryRunStore
from hiveplane.execution.tools import ToolCallRequest, ToolGateway
from hiveplane.policy.approvals import ApprovalService
from hiveplane.policy.engine import PolicyEngine
from hiveplane.policy.packs import InMemoryPolicyPackStore
from hiveplane.policy.store import InMemoryApprovalStore
from hiveplane.shaping.injection import InjectionScanner
from hiveplane.shaping.pipeline import ShapingPipeline
from telemetry import SpanRecorder
from test_adapter_raw_worker import _adapter, _context, _Reporter
from test_certification_workflow import _TASKS, _setup, _write_corpus
from test_execution_admission import _Cert, _pipeline
from test_execution_admission import _run as _admission_run
from test_execution_service import _service

_FIXED_NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _Runs:
    def __init__(self, run: Run) -> None:
        self._run = run
        self.events: list[tuple[EventType, str, str | None]] = []
        self.interventions: list[InterventionAction] = []

    def get(self, run_id: str) -> Run:
        return self._run

    def intervene(self, run_id: str, action: InterventionAction, *, actor: str) -> Run:
        self.interventions.append(action)
        return self._run

    def record_event(
        self, run_id: str, event_type: EventType, actor: str, *, detail: str | None = None
    ) -> None:
        self.events.append((event_type, actor, detail))


def _run(state: RunState = RunState.RUNNING) -> Run:
    return Run(
        id="run-1",
        workload_id="agent-1",
        caller="cli",
        state=state,
        model_identity="openai/gpt-4o/2024-08-06",
        created_at=_FIXED_NOW,
        updated_at=_FIXED_NOW,
        context=AdmissionContext.STAGING,
    )


def _workload(
    make_manifest: Callable[..., AgentWorkload], *, max_bytes: int = 10
) -> AgentWorkload:
    return make_manifest(
        name="agent-1",
        team="payments",
        status="provisional",
        tools={
            "allow": [
                {"tool_id": "mcp.t.read", "trust_level": "read_only"},
                {
                    "tool_id": "mcp.t.destructive",
                    "trust_level": "destructive",
                    "require_approval": True,
                },
            ],
            "deny": ["mcp.t.denied"],
        },
        output_shaping={
            "max_bytes": max_bytes,
            "truncate_strategy": "head",
            "injection_scan": True,
        },
        sandbox={
            "enabled": True,
            "resource_caps": {"memory_mb": 128, "cpu_cores": 1.0, "wall_clock_s": 10},
            "egress": {"allow": ["api.example.com"], "mode": "restricted"},
        },
    )


def _gateway(workload: AgentWorkload, runs: _Runs) -> ToolGateway:
    registry = SimpleNamespace(get=lambda name: SimpleNamespace(manifest=workload))
    return ToolGateway(
        registry,  # type: ignore[arg-type]
        PolicyEngine(InMemoryPolicyPackStore(), clock=lambda: _FIXED_NOW),
        runs,
        shaping=ShapingPipeline(InjectionScanner()),
        approvals=ApprovalService(InMemoryApprovalStore(), clock=lambda: _FIXED_NOW),
        clock=lambda: _FIXED_NOW,
    )


def test_tool_gateway_emits_tool_call_span(
    telemetry_spans: SpanRecorder, make_manifest: Callable[..., AgentWorkload]
) -> None:
    gateway = _gateway(_workload(make_manifest), _Runs(_run()))

    gateway.invoke("run-1", ToolCallRequest(tool_id="mcp.t.read"))

    recorded = telemetry_spans.find("tool_call")
    assert recorded.attributes is not None
    assert recorded.attributes["run_id"] == "run-1"
    assert recorded.attributes["workload"] == "agent-1"
    assert recorded.attributes["team"] == "payments"
    assert recorded.attributes["tool_id"] == "mcp.t.read"
    assert recorded.attributes["outcome"] == "allowed"


def test_tool_call_span_records_denial(
    telemetry_spans: SpanRecorder, make_manifest: Callable[..., AgentWorkload]
) -> None:
    gateway = _gateway(_workload(make_manifest), _Runs(_run()))

    gateway.invoke("run-1", ToolCallRequest(tool_id="mcp.t.denied"))

    recorded = telemetry_spans.find("tool_call")
    assert recorded.attributes is not None
    assert recorded.attributes["outcome"] == "denied"
    assert recorded.attributes["rule"] == "manifest.deny"


def test_tool_call_span_nests_policy_decision(
    telemetry_spans: SpanRecorder, make_manifest: Callable[..., AgentWorkload]
) -> None:
    gateway = _gateway(_workload(make_manifest), _Runs(_run()))

    gateway.invoke("run-1", ToolCallRequest(tool_id="mcp.t.read"))

    decision = telemetry_spans.find("policy_decision")
    tool_call = telemetry_spans.find("tool_call")
    assert decision.parent is not None
    assert decision.parent.span_id == tool_call.context.span_id
    assert decision.attributes is not None
    assert decision.attributes["rule"] == "manifest.allow"
    assert decision.attributes["decision"] == "allow"


def test_policy_engine_emits_policy_decision_span(
    telemetry_spans: SpanRecorder, make_manifest: Callable[..., AgentWorkload]
) -> None:
    workload = _workload(make_manifest)
    engine = PolicyEngine(InMemoryPolicyPackStore(), clock=lambda: _FIXED_NOW)

    engine.evaluate(
        PolicyContext(
            run_id="run-1",
            workload=workload.name,
            team=workload.team,
            environment=AdmissionContext.SANDBOX,
            tool_id="mcp.t.read",
            tools=workload.spec.tools,
        )
    )

    recorded = telemetry_spans.find("policy_decision")
    assert recorded.attributes is not None
    assert recorded.attributes["run_id"] == "run-1"
    assert recorded.attributes["workload"] == "agent-1"
    assert recorded.attributes["decision"] == "allow"


def test_admission_pipeline_emits_admission_span(
    telemetry_spans: SpanRecorder, make_manifest: Callable[..., AgentWorkload]
) -> None:
    workload = make_manifest(name="agent-1", team="payments")

    _pipeline(cert=_Cert(True, "m1")).check(
        _admission_run(AdmissionContext.PRODUCTION), workload
    )

    recorded = telemetry_spans.find("admission")
    assert recorded.attributes is not None
    assert recorded.attributes["run_id"] == "run-1"
    assert recorded.attributes["workload"] == "agent-1"
    assert recorded.attributes["team"] == "payments"
    assert recorded.attributes["outcome"] == "admitted"


def test_admission_span_records_refusal(
    telemetry_spans: SpanRecorder, make_manifest: Callable[..., AgentWorkload]
) -> None:
    _pipeline(cert=_Cert(False)).check(
        _admission_run(AdmissionContext.PRODUCTION), make_manifest(name="agent-1")
    )

    recorded = telemetry_spans.find("admission")
    assert recorded.attributes is not None
    assert recorded.attributes["outcome"] == "refused"


def test_adapter_execution_emits_execution_span(
    telemetry_spans: SpanRecorder, make_manifest: Callable[..., AgentWorkload]
) -> None:
    adapter = _adapter(lambda task, ctx: {"ok": True}, _Reporter())
    workload = make_manifest(name="agent-1", team="payments")

    adapter.submit(_context(workload))

    recorded = telemetry_spans.find("execution")
    assert recorded.attributes is not None
    assert recorded.attributes["run_id"] == "run-1"
    assert recorded.attributes["workload"] == "agent-1"
    assert recorded.attributes["team"] == "payments"
    assert recorded.attributes["outcome"] == "completed"


def test_adapter_execution_span_records_failure(
    telemetry_spans: SpanRecorder, make_manifest: Callable[..., AgentWorkload]
) -> None:
    def boom(task: object, ctx: object) -> object:
        raise RuntimeError("kaboom")

    adapter = _adapter(boom, _Reporter())

    adapter.submit(_context(make_manifest(name="agent-1")))

    recorded = telemetry_spans.find("execution")
    assert recorded.attributes is not None
    assert recorded.attributes["outcome"] == "failed"


def test_propagate_context_links_child_to_captured_parent(
    telemetry_spans: SpanRecorder,
) -> None:
    from hiveplane import telemetry

    def emit_child() -> None:
        with telemetry.span("child"):
            pass

    with telemetry.span("request"):
        work = telemetry.propagate_context(emit_child)
    work()

    child = telemetry_spans.find("child")
    request = telemetry_spans.find("request")
    assert child.parent is not None
    assert child.parent.span_id == request.context.span_id


def test_record_usage_emits_model_call_span(
    telemetry_spans: SpanRecorder, make_manifest: Callable[..., AgentWorkload]
) -> None:
    service, _, _, workload = _service(make_manifest)
    run = service.submit(
        workload=workload,
        caller="cli",
        context=AdmissionContext.PRODUCTION,
        model_identity="m1",
    )

    service.record_usage(
        run.id,
        UsageReport(
            run_id=run.id,
            input_tokens=10,
            output_tokens=5,
            tool_calls=0,
            cost_usd=0.02,
            timestamp=_FIXED_NOW,
            model_identity="m1",
        ),
    )

    recorded = telemetry_spans.find("model_call")
    assert recorded.attributes is not None
    assert recorded.attributes["run_id"] == "run-1"
    assert recorded.attributes["workload"] == "agent-1"
    assert recorded.attributes["model_identity"] == "m1"
    assert recorded.attributes["input_tokens"] == 10
    assert recorded.attributes["output_tokens"] == 5
    assert recorded.attributes["outcome"] == "recorded"


def test_approval_request_and_decision_emit_spans(telemetry_spans: SpanRecorder) -> None:
    approvals = ApprovalService(InMemoryApprovalStore(), clock=lambda: _FIXED_NOW)

    record = approvals.request(
        run_id="run-1",
        workload="agent-1",
        rule="trust.destructive",
        reason="destructive tools require approval",
    )
    approvals.decide(
        record.approval_id, status=ApprovalStatus.APPROVED, operator="alice"
    )

    requested, decided = telemetry_spans.find_all("approval")
    assert requested.attributes is not None
    assert requested.attributes["run_id"] == "run-1"
    assert requested.attributes["workload"] == "agent-1"
    assert requested.attributes["rule"] == "trust.destructive"
    assert requested.attributes["operation"] == "request"
    assert decided.attributes is not None
    assert decided.attributes["status"] == "approved"
    assert decided.attributes["operator"] == "alice"


class _Transport:
    def send(self, destination: object, message: object) -> None:
        pass


def test_fanout_emits_span_per_destination(
    telemetry_spans: SpanRecorder, make_manifest: Callable[..., AgentWorkload]
) -> None:
    workload = make_manifest(
        name="agent-1",
        team="payments",
        fan_out={"on_completed": [{"type": "slack", "channel": "#ops"}]},
    )
    store = InMemoryRunStore()
    run = _run(RunState.COMPLETED)
    store.save_run(run)
    service = FanOutService(
        store, {FanOutType.SLACK: _Transport()}, clock=lambda: _FIXED_NOW
    )

    service.notify(run, workload)

    recorded = telemetry_spans.find("fan_out")
    assert recorded.attributes is not None
    assert recorded.attributes["run_id"] == "run-1"
    assert recorded.attributes["workload"] == "agent-1"
    assert recorded.attributes["team"] == "payments"
    assert recorded.attributes["destination_type"] == "slack"
    assert recorded.attributes["status"] == "delivered"


def test_certification_emits_span(
    telemetry_spans: SpanRecorder,
    make_manifest: Callable[..., AgentWorkload],
    tmp_path: Path,
) -> None:
    corpora_dir = _write_corpus(tmp_path / "corpora", _TASKS)
    _, _, coordinator = _setup(make_manifest, corpora_dir)

    coordinator.certify("repo-agent", target_context=TargetContext.STAGING)

    recorded = telemetry_spans.find("certification")
    assert recorded.attributes is not None
    assert recorded.attributes["workload"] == "repo-agent"
    assert recorded.attributes["target_context"] == "staging"
    assert "benchmark_run_id" in recorded.attributes


def test_http_request_emits_server_span(telemetry_spans: SpanRecorder) -> None:
    from fastapi.testclient import TestClient
    from opentelemetry.trace import SpanKind

    from hiveplane.api.app import create_app

    client = TestClient(create_app())

    response = client.get("/healthz")

    assert response.status_code == 200
    recorded = telemetry_spans.find("GET /healthz")
    assert recorded.kind is SpanKind.SERVER
    assert recorded.attributes is not None
    assert recorded.attributes["http.response.status_code"] == 200


def test_start_run_links_api_call_to_adapter_execution(
    telemetry_spans: SpanRecorder, make_manifest: Callable[..., AgentWorkload]
) -> None:
    from fastapi.testclient import TestClient

    from hiveplane.adapters.base import AdapterRunExecutor
    from hiveplane.api.app import create_app

    app = create_app()
    app.state.registry_service.create(
        make_manifest(
            name="agent-1",
            sandbox={
                "enabled": True,
                "resource_caps": {"memory_mb": 128, "cpu_cores": 1.0, "wall_clock_s": 10},
            },
        )
    )
    adapter = _adapter(lambda task, ctx: {"ok": True}, _Reporter())
    app.state.run_service.attach_executor(AdapterRunExecutor(adapter))
    client = TestClient(app)

    submitted = client.post(
        "/runs",
        json={
            "workload": "agent-1",
            "caller": "cli",
            "context": "sandbox",
            "task": {"x": 1},
            "model_identity": "openai/gpt-4o/2024-08-06",
        },
    )
    assert submitted.status_code == 201, submitted.text
    run_id = submitted.json()["id"]
    started = client.post(f"/runs/{run_id}/start")
    assert started.status_code == 200, started.text

    execution = telemetry_spans.find("execution")
    request = telemetry_spans.find(f"POST /runs/{run_id}/start")
    assert execution.parent is not None
    assert execution.parent.span_id == request.context.span_id


def test_sandbox_provision_emits_span(
    telemetry_spans: SpanRecorder, make_manifest: Callable[..., AgentWorkload]
) -> None:
    from fastapi.testclient import TestClient

    from hiveplane.api.app import create_app

    app = create_app()
    app.state.registry_service.create(
        make_manifest(
            name="agent-1",
            team="payments",
            sandbox={
                "enabled": True,
                "resource_caps": {"memory_mb": 128, "cpu_cores": 1.0, "wall_clock_s": 10},
            },
        )
    )
    client = TestClient(app)
    run_id = client.post(
        "/runs",
        json={
            "workload": "agent-1",
            "caller": "cli",
            "context": "sandbox",
            "model_identity": "openai/gpt-4o/2024-08-06",
        },
    ).json()["id"]

    started = client.post(f"/runs/{run_id}/start")

    assert started.status_code == 200, started.text
    recorded = telemetry_spans.find("sandbox")
    assert recorded.attributes is not None
    assert recorded.attributes["run_id"] == run_id
    assert recorded.attributes["workload"] == "agent-1"
    assert recorded.attributes["team"] == "payments"
    assert recorded.attributes["operation"] == "provision"


class _FakeProvider:
    def __init__(self) -> None:
        self.flushed = False

    def force_flush(self, timeout_millis: int = 30000) -> bool:
        self.flushed = True
        return True


def test_app_lifespan_installs_and_flushes_telemetry(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from fastapi.testclient import TestClient

    from hiveplane import telemetry
    from hiveplane.api.app import create_app

    provider = _FakeProvider()
    meter_provider = _FakeProvider()
    installed: list[object] = []

    def _install(settings: object) -> _FakeProvider:
        installed.append(settings)
        return provider

    def _install_metrics(settings: object) -> _FakeProvider:
        installed.append(settings)
        return meter_provider

    monkeypatch.setattr(telemetry, "configure_telemetry", _install)
    monkeypatch.setattr(telemetry, "configure_metrics", _install_metrics)

    with TestClient(create_app()) as client:
        assert client.get("/healthz").status_code == 200

    assert installed
    assert provider.flushed is True
    assert meter_provider.flushed is True
