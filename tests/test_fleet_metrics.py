"""Tests that the live path emits core fleet metrics (M20, #50)."""

from __future__ import annotations

from collections.abc import Callable

from hiveplane.core.decision import DecisionOutcome, PolicyContext
from hiveplane.core.run import AdmissionContext
from hiveplane.core.usage import UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.models import InterventionAction
from hiveplane.execution.service import RunService
from hiveplane.execution.tools import ToolCallRequest
from hiveplane.policy.engine import PolicyEngine
from hiveplane.policy.packs import InMemoryPolicyPackStore
from metrics import RecordingMetrics
from test_execution_admission import _Cert, _pipeline
from test_execution_admission import _run as _admission_run
from test_execution_service import _service
from test_telemetry_instrumentation import _gateway, _run, _Runs, _workload


def _submit_and_start(
    make_manifest: Callable[..., AgentWorkload],
    *,
    policy_outcome: DecisionOutcome = DecisionOutcome.ALLOW,
) -> tuple[RunService, str, str]:
    service, _, _, workload = _service(make_manifest, policy_outcome=policy_outcome)
    run = service.submit(
        workload=workload,
        caller="cli",
        context=AdmissionContext.PRODUCTION,
        model_identity="m1",
    )
    service.start(run.id, actor="cli")
    return service, run.id, workload


def test_submit_and_start_record_run_state(
    make_manifest: Callable[..., AgentWorkload], fleet_metrics: RecordingMetrics
) -> None:
    _submit_and_start(make_manifest)

    states = [call["state"] for call in fleet_metrics.called("record_run_state")]
    assert "queued" in states
    assert "running" in states
    assert all(call["workload"] == "agent-1" for call in fleet_metrics.called("record_run_state"))
    assert all(call["team"] == "platform" for call in fleet_metrics.called("record_run_state"))


def test_failed_run_records_failure_and_duration(
    make_manifest: Callable[..., AgentWorkload], fleet_metrics: RecordingMetrics
) -> None:
    service, run_id, _ = _submit_and_start(make_manifest)

    service.fail(run_id, actor="cli", reason="boom")

    assert fleet_metrics.called("record_failure") == [
        {"workload": "agent-1", "team": "platform", "reason": "boom"}
    ]
    durations = fleet_metrics.called("record_run_duration")
    assert durations == [{"workload": "agent-1", "team": "platform", "seconds": 0.0}]


def test_escalation_records_metric(
    make_manifest: Callable[..., AgentWorkload], fleet_metrics: RecordingMetrics
) -> None:
    service, _, _, workload = _service(
        make_manifest, policy_outcome=DecisionOutcome.ESCALATE
    )

    service.submit(
        workload=workload,
        caller="cli",
        context=AdmissionContext.PRODUCTION,
        model_identity="m1",
    )

    assert fleet_metrics.called("record_escalation") == [
        {"workload": "agent-1", "team": "platform", "rule": "test"}
    ]


def test_intervention_records_latency(
    make_manifest: Callable[..., AgentWorkload], fleet_metrics: RecordingMetrics
) -> None:
    service, run_id, _ = _submit_and_start(make_manifest)

    service.intervene(run_id, InterventionAction.PAUSE, actor="operator")

    assert fleet_metrics.called("record_intervention_latency") == [
        {"workload": "agent-1", "seconds": 0.0}
    ]


def test_record_usage_records_budget_burn(
    make_manifest: Callable[..., AgentWorkload], fleet_metrics: RecordingMetrics
) -> None:
    service, run_id, _ = _submit_and_start(make_manifest)

    service.record_usage(
        run_id,
        UsageReport(
            run_id=run_id,
            input_tokens=0,
            output_tokens=0,
            tool_calls=0,
            cost_usd=0.02,
            timestamp=_admission_run(AdmissionContext.PRODUCTION).created_at,
            model_identity="m1",
        ),
    )

    assert fleet_metrics.called("record_budget_burn") == [
        {"workload": "agent-1", "team": "platform", "run_id": run_id, "cost_usd": 0.02}
    ]


def test_policy_engine_records_decision(
    make_manifest: Callable[..., AgentWorkload], fleet_metrics: RecordingMetrics
) -> None:
    workload = make_manifest(name="agent-1", team="payments")
    engine = PolicyEngine(InMemoryPolicyPackStore())

    engine.evaluate(
        PolicyContext(
            run_id="run-1",
            workload="agent-1",
            team="payments",
            environment=AdmissionContext.SANDBOX,
            tool_id="mcp.t.read",
            tools=workload.spec.tools,
        )
    )

    assert fleet_metrics.called("record_policy_decision") == [
        {
            "workload": "agent-1",
            "team": "payments",
            "decision": "deny",
            "rule": "default.deny",
        }
    ]


def test_tool_gateway_records_tool_call(
    make_manifest: Callable[..., AgentWorkload], fleet_metrics: RecordingMetrics
) -> None:
    gateway = _gateway(_workload(make_manifest), _Runs(_run()))

    gateway.invoke("run-1", ToolCallRequest(tool_id="mcp.t.read"))

    assert fleet_metrics.called("record_tool_call") == [
        {
            "workload": "agent-1",
            "team": "payments",
            "tool_id": "mcp.t.read",
            "trust_level": "read_only",
            "outcome": "allowed",
        }
    ]


def test_model_swap_records_block(
    make_manifest: Callable[..., AgentWorkload], fleet_metrics: RecordingMetrics
) -> None:
    pipeline = _pipeline(cert=_Cert(True, "other-model"))

    pipeline.check(
        _admission_run(AdmissionContext.PRODUCTION, model="m1"),
        make_manifest(name="agent-1", team="payments"),
    )

    assert fleet_metrics.called("record_model_swap_block") == [{"workload": "agent-1"}]
