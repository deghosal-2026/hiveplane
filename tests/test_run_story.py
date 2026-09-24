"""Tests for the run execution story and trace-linked debug context (M20, #51)."""

from __future__ import annotations

from collections.abc import Callable

from fastapi.testclient import TestClient

from hiveplane import telemetry
from hiveplane.api.app import create_app
from hiveplane.core.approval import ApprovalRecord
from hiveplane.core.run import AdmissionContext, Run
from hiveplane.core.usage import UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.service import RunService
from hiveplane.policy.approvals import ApprovalService
from hiveplane.policy.store import InMemoryApprovalStore
from telemetry import SpanRecorder
from test_execution_service import _service


def _submit(make_manifest: Callable[..., AgentWorkload]) -> tuple[RunService, str, Run]:
    service, _, _, workload = _service(make_manifest)
    run = service.submit(
        workload=workload,
        caller="cli",
        context=AdmissionContext.PRODUCTION,
        model_identity="m1",
    )
    return service, run.id, run


def test_story_orders_admission_state_and_usage(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, run_id, run = _submit(make_manifest)
    service.start(run_id, actor="cli")
    service.record_usage(
        run_id,
        UsageReport(
            run_id=run_id,
            input_tokens=10,
            output_tokens=5,
            tool_calls=0,
            cost_usd=0.02,
            timestamp=run.created_at,
            model_identity="m1",
        ),
    )

    story = service.story(run_id)

    assert story.run_id == run_id
    assert story.workload == "agent-1"
    assert story.team == "platform"
    assert story.state.value == "running"
    assert story.certification_status == "uncertified"
    kinds = [entry.kind for entry in story.entries]
    assert kinds[0] == "admission"
    assert "state" in kinds
    assert "model_call" in kinds


def test_story_includes_model_call_detail(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, run_id, run = _submit(make_manifest)
    service.record_usage(
        run_id,
        UsageReport(
            run_id=run_id,
            input_tokens=10,
            output_tokens=5,
            tool_calls=0,
            cost_usd=0.02,
            timestamp=run.created_at,
            model_identity="m1",
            prompt="classify this PR",
            response="risky",
            latency_ms=42,
        ),
    )

    story = service.story(run_id)

    entry = next(entry for entry in story.entries if entry.kind == "model_call")
    assert entry.detail["prompt"] == "classify this PR"
    assert entry.detail["response"] == "risky"
    assert entry.detail["latency_ms"] == 42


def test_story_links_the_active_trace(
    telemetry_spans: SpanRecorder, make_manifest: Callable[..., AgentWorkload]
) -> None:
    service, _, _, workload = _service(make_manifest)
    with telemetry.span("request"):
        run = service.submit(
            workload=workload,
            caller="cli",
            context=AdmissionContext.PRODUCTION,
            model_identity="m1",
        )
        service.start(run.id, actor="cli")

    story = service.story(run.id)

    assert story.trace_id is not None
    assert len(story.trace_id) == 32


def test_story_includes_approvals(make_manifest: Callable[..., AgentWorkload]) -> None:
    service, run_id, run = _submit(make_manifest)
    approval = ApprovalRecord(
        approval_id="ap-1",
        run_id=run_id,
        workload="agent-1",
        rule="trust.destructive",
        reason="destructive tools require approval",
        requested_at=run.created_at,
    )

    story = service.story(run_id, approvals=[approval])

    approvals = [entry for entry in story.entries if entry.kind == "approval"]
    assert len(approvals) == 1
    assert approvals[0].summary == "trust.destructive pending"


def test_approvals_can_be_filtered_by_run() -> None:
    approvals = ApprovalService(InMemoryApprovalStore())
    approvals.request(run_id="run-1", workload="agent-1", rule="r1", reason="x")
    approvals.request(run_id="run-2", workload="agent-1", rule="r2", reason="y")

    filtered = approvals.list(run_id="run-1")

    assert [record.run_id for record in filtered] == ["run-1"]


def test_run_story_endpoint(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
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
    client.post(f"/runs/{run_id}/start")

    response = client.get(f"/runs/{run_id}/story")

    assert response.status_code == 200
    body = response.json()
    assert body["run_id"] == run_id
    assert body["workload"] == "agent-1"
    assert any(entry["kind"] == "admission" for entry in body["entries"])
