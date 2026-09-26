"""Pipeline engine tests: linear, fan-out/in, gates, budget, failure, retry (M29)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError

from hiveplane.core.run import AdmissionContext, PipelineOrigin
from hiveplane.fleet.pipelines import PipelineState
from hiveplane.pipelines.engine import PipelineEngine, PipelineRunNotFoundError
from hiveplane.pipelines.models import NodeResult, NodeStatus
from hiveplane.pipelines.spec import ApprovalTiming, OnExceed, PipelineSpec
from hiveplane.pipelines.store import InMemoryPipelineStore
from hiveplane.tenancy import TenantContext

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


class FakeExecutor:
    """Synchronous child-run executor with a per-workload result factory."""

    def __init__(
        self,
        factory: Callable[[str, dict[str, Any]], NodeResult] | None = None,
    ) -> None:
        self._factory = factory or (
            lambda workload, inputs: NodeResult(status=NodeStatus.COMPLETED, output={"ok": True})
        )
        self.submissions: list[dict[str, Any]] = []
        self.cancelled: list[str] = []
        self._results: dict[str, NodeResult] = {}

    def submit(
        self,
        *,
        workload: str,
        inputs: dict[str, Any],
        context: AdmissionContext,
        origin: PipelineOrigin,
        ctx: TenantContext,
    ) -> str:
        run_id = f"run-{len(self.submissions) + 1}"
        self.submissions.append(
            {"run_id": run_id, "workload": workload, "inputs": inputs, "origin": origin}
        )
        self._results[run_id] = self._factory(workload, inputs)
        return run_id

    def result(self, child_run_id: str, *, ctx: TenantContext) -> NodeResult | None:
        return self._results.pop(child_run_id, None)

    def cancel(self, child_run_id: str, *, ctx: TenantContext) -> None:
        self.cancelled.append(child_run_id)


class FakeGate:
    def __init__(self) -> None:
        self.statuses: dict[str, str] = {}

    def request(self, *, run_id: str, workload: str, rule: str, reason: str) -> str:
        approval_id = f"appr-{len(self.statuses) + 1}"
        self.statuses[approval_id] = "pending"
        return approval_id

    def status(self, approval_id: str) -> str:
        return self.statuses.get(approval_id, "pending")

    def approve(self, approval_id: str) -> None:
        self.statuses[approval_id] = "approved"


def _engine(
    executor: FakeExecutor | None = None,
    gate: FakeGate | None = None,
    schemas: Callable[[str], Any] | None = None,
) -> tuple[PipelineEngine, InMemoryPipelineStore, FakeExecutor]:
    store = InMemoryPipelineStore()
    fake = executor or FakeExecutor()
    counter = __import__("itertools").count(1)
    engine = PipelineEngine(
        store,
        fake,
        approvals=gate,
        schema_lookup=schemas,
        budget_lookup=lambda workload: 0.1,
        clock=lambda: _NOW,
        id_factory=lambda: f"pr-{next(counter)}",
    )
    return engine, store, fake


def _linear() -> PipelineSpec:
    return PipelineSpec.model_validate(
        {
            "id": "incident-response",
            "name": "Incident response",
            "nodes": [
                {"id": "triage", "kind": "workload", "workload": "triage"},
                {
                    "id": "remediate",
                    "kind": "workload",
                    "workload": "remediate",
                    "inputs": {"diagnosis": "${triage.output.diagnosis}"},
                },
                {
                    "id": "notify",
                    "kind": "workload",
                    "workload": "notify",
                    "inputs": {"summary": "${remediate.output.summary}"},
                },
            ],
            "edges": [
                {"from": "triage", "to": "remediate"},
                {"from": "remediate", "to": "notify"},
            ],
        }
    )


def _outputs(workload: str, inputs: dict[str, Any]) -> NodeResult:
    return {
        "triage": NodeResult(
            status=NodeStatus.COMPLETED, output={"diagnosis": "db down"}, cost_usd=0.1
        ),
        "remediate": NodeResult(
            status=NodeStatus.COMPLETED,
            output={"summary": f"fixed {inputs.get('diagnosis')}"},
            cost_usd=0.1,
        ),
        "notify": NodeResult(status=NodeStatus.COMPLETED, output={"sent": True}, cost_usd=0.1),
    }[workload]


def test_linear_pipeline_runs_end_to_end_with_handoffs() -> None:
    engine, store, fake = _engine(FakeExecutor(_outputs))
    header = engine.submit(_linear())
    assert header.state is PipelineState.COMPLETED
    assert [s["workload"] for s in fake.submissions] == ["triage", "remediate", "notify"]
    assert fake.submissions[1]["inputs"] == {"diagnosis": "db down"}
    assert fake.submissions[2]["inputs"] == {"summary": "fixed db down"}
    assert fake.submissions[0]["origin"].pipeline_id == "incident-response"
    assert fake.submissions[0]["origin"].parent_run_id == header.parent_run_id
    nodes = {n.node_id: n for n in store.list_node_runs(header.pipeline_run_id)}
    assert all(n.status is NodeStatus.COMPLETED for n in nodes.values())
    assert header.spent_usd == pytest.approx(0.3)


def test_timeline_reports_node_status_and_cost() -> None:
    engine, _, _ = _engine(FakeExecutor(_outputs))
    header = engine.submit(_linear())
    timeline = engine.timeline(header.pipeline_run_id)
    assert timeline.pipeline_id == "incident-response"
    assert [node.node_id for node in timeline.nodes] == ["triage", "remediate", "notify"]
    assert all(node.cost_usd == pytest.approx(0.1) for node in timeline.nodes)


def test_cycle_is_rejected_at_spec_validation() -> None:
    with pytest.raises(ValidationError):
        PipelineSpec.model_validate(
            {
                "id": "p",
                "name": "p",
                "nodes": [
                    {"id": "a", "kind": "workload", "workload": "a"},
                    {"id": "b", "kind": "workload", "workload": "b"},
                ],
                "edges": [
                    {"from": "a", "to": "b"},
                    {"from": "b", "to": "a"},
                ],
            }
        )


def test_handoff_schema_mismatch_fails_the_node() -> None:
    from hiveplane.core.spec import IOSpec

    schemas = {
        "remediate": IOSpec(input_schema={"type": "object", "required": ["diagnosis"]})
    }
    engine, store, fake = _engine(
        FakeExecutor(_outputs), schemas=lambda workload: schemas.get(workload)
    )
    # Remove the diagnosis input so the consumer schema is violated.
    spec = _linear()
    spec.nodes[1].inputs = {}
    header = engine.submit(spec)
    assert header.state is PipelineState.FAILED
    assert len(fake.submissions) == 1  # notify never submitted
    nodes = {n.node_id: n for n in store.list_node_runs(header.pipeline_run_id)}
    assert nodes["remediate"].status is NodeStatus.FAILED
    assert "remediate" in (nodes["remediate"].error or "")


def test_approval_gate_pauses_then_resumes() -> None:
    gate = FakeGate()
    engine, store, fake = _engine(FakeExecutor(_outputs), gate=gate)
    spec = _linear()
    spec.nodes[1].requires_approval = ApprovalTiming.BEFORE
    header = engine.submit(spec)
    assert header.state is PipelineState.PAUSED
    assert [s["workload"] for s in fake.submissions] == ["triage"]
    nodes = {n.node_id: n for n in store.list_node_runs(header.pipeline_run_id)}
    assert nodes["remediate"].status is NodeStatus.WAITING_APPROVAL
    gate.approve(nodes["remediate"].approval_id or "")
    header = engine.advance(header.pipeline_run_id)
    assert header.state is PipelineState.COMPLETED
    assert [s["workload"] for s in fake.submissions] == ["triage", "remediate", "notify"]


def test_budget_exceeded_pauses_before_overspend() -> None:
    engine, _store, fake = _engine(FakeExecutor(_outputs))
    spec = _linear()
    spec.budget.usd = 0.25  # each node costs 0.1; the third would exceed
    header = engine.submit(spec)
    assert header.state is PipelineState.PAUSED
    assert len(fake.submissions) == 2
    assert header.spent_usd <= header.budget_usd


def test_budget_on_exceed_fail() -> None:
    engine, _, _ = _engine(FakeExecutor(_outputs))
    spec = _linear()
    spec.budget.usd = 0.25
    spec.budget.on_exceed = OnExceed.FAIL
    header = engine.submit(spec)
    assert header.state is PipelineState.FAILED


def test_fail_fast_stops_and_skips_downstream() -> None:
    def factory(workload: str, inputs: dict[str, Any]) -> NodeResult:
        if workload == "triage":
            return NodeResult(status=NodeStatus.FAILED, error="boom")
        return NodeResult(status=NodeStatus.COMPLETED, output={"ok": True})

    engine, store, fake = _engine(FakeExecutor(factory))
    header = engine.submit(_linear())
    assert header.state is PipelineState.FAILED
    assert [s["workload"] for s in fake.submissions] == ["triage"]
    nodes = {n.node_id: n for n in store.list_node_runs(header.pipeline_run_id)}
    assert nodes["remediate"].status is NodeStatus.SKIPPED
    assert nodes["notify"].status is NodeStatus.SKIPPED


def test_continue_policy_lets_independent_nodes_finish() -> None:
    def factory(workload: str, inputs: dict[str, Any]) -> NodeResult:
        if workload == "remediate":
            return NodeResult(status=NodeStatus.FAILED, error="boom")
        return NodeResult(status=NodeStatus.COMPLETED, output={"ok": True})

    spec = PipelineSpec.model_validate(
        {
            "id": "p",
            "name": "p",
            "nodes": [
                {"id": "triage", "kind": "workload", "workload": "triage"},
                {
                    "id": "remediate",
                    "kind": "workload",
                    "workload": "remediate",
                    "on_failure": "continue",
                },
                {"id": "notify", "kind": "workload", "workload": "notify"},
            ],
            "edges": [
                {"from": "triage", "to": "remediate"},
                {"from": "triage", "to": "notify"},
            ],
        }
    )
    engine, store, fake = _engine(FakeExecutor(factory))
    header = engine.submit(spec)
    assert header.state is PipelineState.FAILED
    assert {s["workload"] for s in fake.submissions} == {"triage", "remediate", "notify"}
    nodes = {n.node_id: n for n in store.list_node_runs(header.pipeline_run_id)}
    assert nodes["notify"].status is NodeStatus.COMPLETED
    assert nodes["remediate"].status is NodeStatus.FAILED


def test_node_retry_reexecutes_until_success() -> None:
    attempts = {"n": 0}

    def factory(workload: str, inputs: dict[str, Any]) -> NodeResult:
        if workload == "triage":
            attempts["n"] += 1
            if attempts["n"] == 1:
                return NodeResult(status=NodeStatus.FAILED, error="transient")
            return NodeResult(status=NodeStatus.COMPLETED, output={"diagnosis": "ok"})
        return _outputs(workload, inputs)

    spec = _linear()
    spec.nodes[0].retry.max_attempts = 2
    engine, store, _fake = _engine(FakeExecutor(factory))
    header = engine.submit(spec)
    assert header.state is PipelineState.COMPLETED
    assert attempts["n"] == 2
    triage = store.get_node_run(header.pipeline_run_id, "triage", 2)
    assert triage is not None and triage.status is NodeStatus.COMPLETED


def test_retry_node_replays_a_failed_node() -> None:
    state = {"fail": True}

    def factory(workload: str, inputs: dict[str, Any]) -> NodeResult:
        if workload == "triage" and state["fail"]:
            return NodeResult(status=NodeStatus.FAILED, error="boom")
        return _outputs(workload, inputs)

    engine, _store, _fake = _engine(FakeExecutor(factory))
    header = engine.submit(_linear())
    assert header.state is PipelineState.FAILED
    state["fail"] = False
    header = engine.retry_node(header.pipeline_run_id, "triage")
    assert header.state is PipelineState.COMPLETED


def test_fan_out_and_fan_in() -> None:
    spec = PipelineSpec.model_validate(
        {
            "id": "fan",
            "name": "fan",
            "nodes": [
                {"id": "triage", "kind": "workload", "workload": "triage"},
                {
                    "id": "fanout",
                    "kind": "fan_out",
                    "over": "${triage.output.services}",
                    "as": "service",
                    "node": "remediate",
                },
                {
                    "id": "remediate",
                    "kind": "workload",
                    "workload": "remediate",
                    "inputs": {"service": "${service}"},
                },
                {
                    "id": "summary",
                    "kind": "fan_in",
                    "from": ["fanout"],
                    "reducer": "json_merge",
                },
            ],
            "edges": [
                {"from": "triage", "to": "fanout"},
                {"from": "fanout", "to": "remediate"},
                {"from": "fanout", "to": "summary"},
            ],
        }
    )

    def factory(workload: str, inputs: dict[str, Any]) -> NodeResult:
        if workload == "triage":
            return NodeResult(
                status=NodeStatus.COMPLETED, output={"services": ["a", "b"]}, cost_usd=0.1
            )
        return NodeResult(
            status=NodeStatus.COMPLETED,
            output={"service": inputs.get("service"), "fixed": True},
            cost_usd=0.1,
        )

    engine, store, fake = _engine(FakeExecutor(factory))
    header = engine.submit(spec)
    assert header.state is PipelineState.COMPLETED
    remediations = [s for s in fake.submissions if s["workload"] == "remediate"]
    assert [s["inputs"]["service"] for s in remediations] == ["a", "b"]
    summary = store.get_node_run(header.pipeline_run_id, "summary", 1)
    assert summary is not None and summary.output == {
        "a": {"service": "a", "fixed": True},
        "b": {"service": "b", "fixed": True},
    }


def test_missing_pipeline_run_raises() -> None:
    engine, _, _ = _engine()
    with pytest.raises(PipelineRunNotFoundError):
        engine.advance("missing")
