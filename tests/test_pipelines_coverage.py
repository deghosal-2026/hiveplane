"""Comprehensive edge-path tests for M29 pipelines (engine, executor, handoff)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine

from hiveplane.core.run import AdmissionContext, PipelineOrigin, Run, RunState
from hiveplane.execution.models import InterventionAction
from hiveplane.fleet.pipelines import PipelineState
from hiveplane.pipelines.engine import (
    PipelineEngine,
    PipelineRunNotFoundError,
    PipelineSpecNotFoundError,
)
from hiveplane.pipelines.executor import RunNodeExecutor, ServiceApprovalGate
from hiveplane.pipelines.handoff import (
    HandoffError,
    PipelineContext,
    render_ref,
    validate_schema,
)
from hiveplane.pipelines.models import NodeResult, NodeStatus
from hiveplane.pipelines.spec import PipelineSpec
from hiveplane.pipelines.store import (
    InMemoryPipelineStore,
    PostgresPipelineStore,
)
from hiveplane.tenancy import DEFAULT_CONTEXT
from postgres import reset_database
from test_pipelines_engine import FakeExecutor, FakeGate, _engine

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _ok(output: Any = None, cost_usd: float = 0.0) -> Any:
    return lambda w, i: NodeResult(
        status=NodeStatus.COMPLETED, output=output, cost_usd=cost_usd
    )


class AsyncExecutor(FakeExecutor):
    """Returns results only once explicitly completed."""

    def __init__(self, factory: Any = None) -> None:
        super().__init__(factory)
        self._deferred: dict[str, NodeResult] = {}

    def result(self, child_run_id: str, *, ctx: Any) -> NodeResult | None:
        return self._deferred.pop(child_run_id, None)

    def complete(self, child_run_id: str) -> None:
        self._deferred[child_run_id] = self._results[child_run_id]


# --------------------------------------------------------------------------- #
# Engine: gate, transform, reducers, budgets, async
# --------------------------------------------------------------------------- #
def _single(kind: str, **node: Any) -> PipelineSpec:
    return PipelineSpec.model_validate(
        {"id": "p", "name": "p", "nodes": [{"id": "a", "kind": kind, **node}]}
    )


def test_gate_node_without_approvals_fails() -> None:
    spec = _single("gate")
    engine, store, _ = _engine()
    header = engine.submit(spec)
    assert header.state is PipelineState.FAILED
    node = store.get_node_run(header.pipeline_run_id, "a", 1)
    assert node is not None and "approval gate not configured" in (node.error or "")


def test_gate_node_pauses_and_completes() -> None:
    gate = FakeGate()
    engine, store, _ = _engine(gate=gate)
    header = engine.submit(_single("gate"))
    assert header.state is PipelineState.PAUSED
    node = store.get_node_run(header.pipeline_run_id, "a", 1)
    assert node is not None and node.status is NodeStatus.WAITING_APPROVAL
    gate.statuses[node.approval_id or ""] = "denied"
    header = engine.advance(header.pipeline_run_id)
    assert header.state is PipelineState.FAILED
    node = store.get_node_run(header.pipeline_run_id, "a", 1)
    assert node is not None and node.error == "approval denied"


def test_gate_node_approved_completes() -> None:
    gate = FakeGate()
    engine, store, _ = _engine(gate=gate)
    header = engine.submit(_single("gate"))
    node = store.get_node_run(header.pipeline_run_id, "a", 1)
    assert node is not None
    gate.approve(node.approval_id or "")
    header = engine.advance(header.pipeline_run_id)
    assert header.state is PipelineState.COMPLETED


def test_transform_node_renders_output() -> None:
    spec = _single("transform", transform={"greeting": "hi ${event.name}"})
    engine, store, _ = _engine()
    header = engine.submit(spec, inputs={"event": {"name": "world"}})
    assert header.state is PipelineState.COMPLETED
    node = store.get_node_run(header.pipeline_run_id, "a", 1)
    assert node is not None and node.output == {"greeting": "hi world"}


def test_per_step_budget_override_fails_node() -> None:
    spec = _single("workload", workload="triage", budget={"usd": 0.05})
    engine, _, _ = _engine(FakeExecutor(_ok(cost_usd=0.1)))
    header = engine.submit(spec)
    assert header.state is PipelineState.FAILED


def test_async_executor_runs_then_completes() -> None:
    executor = AsyncExecutor(_ok(output={'ok': True}))
    engine, store, _ = _engine(executor)
    header = engine.submit(_single("workload", workload="triage"))
    assert header.state is PipelineState.RUNNING
    node = store.get_node_run(header.pipeline_run_id, "a", 1)
    assert node is not None and node.status is NodeStatus.RUNNING
    executor.complete(node.child_run_id or "")
    header = engine.advance(header.pipeline_run_id)
    assert header.state is PipelineState.COMPLETED


def test_advance_terminal_is_a_noop() -> None:
    engine, _, _ = _engine(FakeExecutor(_ok()))
    header = engine.submit(_single("workload", workload="triage"))
    again = engine.advance(header.pipeline_run_id)
    assert again.state is PipelineState.COMPLETED


def test_advance_unknown_run_raises() -> None:
    engine, _, _ = _engine()
    with pytest.raises(PipelineRunNotFoundError):
        engine.advance("missing")


def test_retry_unknown_node_raises() -> None:
    engine, _, _ = _engine(FakeExecutor(_ok()))
    header = engine.submit(_single("workload", workload="triage"))
    with pytest.raises(PipelineRunNotFoundError):
        engine.retry_node(header.pipeline_run_id, "ghost")


def test_timeline_unknown_run_raises() -> None:
    engine, _, _ = _engine()
    with pytest.raises(PipelineRunNotFoundError):
        engine.timeline("missing")


def test_missing_spec_raises_on_advance() -> None:
    store = InMemoryPipelineStore()
    from hiveplane.pipelines.models import PipelineRunHeader

    store.save_run(
        PipelineRunHeader(
            pipeline_run_id="pr-1",
            pipeline_id="ghost",
            state=PipelineState.RUNNING,
            started_at=_NOW,
        )
    )
    engine = PipelineEngine(store, FakeExecutor())
    with pytest.raises(PipelineSpecNotFoundError):
        engine.advance("pr-1")


@pytest.mark.parametrize(
    ("reducer", "value", "expected"),
    [
        ("concat", [1, 2], [1, 2]),
        ("sum", 5, 5),
        ("first_success", "x", "x"),
    ],
)
def test_fan_in_reducers(reducer: str, value: Any, expected: Any) -> None:
    spec = PipelineSpec.model_validate(
        {
            "id": "p",
            "name": "p",
            "nodes": [
                {"id": "a", "kind": "workload", "workload": "a"},
                {"id": "reduce", "kind": "fan_in", "from": ["a"], "reducer": reducer},
            ],
            "edges": [{"from": "a", "to": "reduce"}],
        }
    )
    engine, store, _ = _engine(
        FakeExecutor(lambda w, i: NodeResult(status=NodeStatus.COMPLETED, output=value))
    )
    header = engine.submit(spec)
    node = store.get_node_run(header.pipeline_run_id, "reduce", 1)
    assert node is not None and node.output == expected


def test_fan_out_over_zero_items() -> None:
    spec = PipelineSpec.model_validate(
        {
            "id": "p",
            "name": "p",
            "nodes": [
                {"id": "a", "kind": "workload", "workload": "a"},
                {
                    "id": "fo",
                    "kind": "fan_out",
                    "over": "${a.output.items}",
                    "as": "item",
                    "node": "child",
                },
                {"id": "child", "kind": "workload", "workload": "child"},
            ],
            "edges": [{"from": "a", "to": "fo"}, {"from": "fo", "to": "child"}],
        }
    )
    engine, store, _ = _engine(
        FakeExecutor(lambda w, i: NodeResult(status=NodeStatus.COMPLETED, output={"items": []}))
    )
    header = engine.submit(spec)
    assert header.state is PipelineState.COMPLETED
    node = store.get_node_run(header.pipeline_run_id, "fo", 1)
    assert node is not None and node.output == {}


def test_fan_out_over_non_list_fails() -> None:
    spec = PipelineSpec.model_validate(
        {
            "id": "p",
            "name": "p",
            "nodes": [
                {"id": "a", "kind": "workload", "workload": "a"},
                {
                    "id": "fo",
                    "kind": "fan_out",
                    "over": "${a.output.items}",
                    "as": "i",
                    "node": "child",
                },
                {"id": "child", "kind": "workload", "workload": "child"},
            ],
            "edges": [{"from": "a", "to": "fo"}, {"from": "fo", "to": "child"}],
        }
    )
    engine, _store, _ = _engine(
        FakeExecutor(lambda w, i: NodeResult(status=NodeStatus.COMPLETED, output={"items": "nope"}))
    )
    header = engine.submit(spec)
    assert header.state is PipelineState.FAILED


# --------------------------------------------------------------------------- #
# Executor / approval adapters
# --------------------------------------------------------------------------- #
def _run(state: RunState, **kwargs: Any) -> Run:
    return Run(
        id="run-1",
        workload_id="agent-1",
        caller="pipeline",
        state=state,
        created_at=_NOW,
        updated_at=_NOW,
        **kwargs,
    )


class _FakeRuns:
    def __init__(self, run: Run) -> None:
        self.run = run
        self.submitted: dict[str, Any] = {}
        self.intervened: tuple[str, InterventionAction] | None = None

    def submit(self, **kwargs: Any) -> Run:
        self.submitted = kwargs
        return self.run

    def get(self, run_id: str, *, ctx: Any) -> Run:
        return self.run

    def intervene(self, run_id: str, action: InterventionAction, *, actor: str, ctx: Any) -> Run:
        self.intervened = (run_id, action)
        return self.run


def test_run_node_executor_submit_result_and_cancel() -> None:
    runs = _FakeRuns(_run(RunState.COMPLETED, result={"ok": True}, cost_usd=0.2))
    executor = RunNodeExecutor(runs)
    origin = PipelineOrigin(pipeline_run_id="pr-1", pipeline_id="p", node_id="a")
    child = executor.submit(
        workload="agent-1",
        inputs={"x": 1},
        context=AdmissionContext.STAGING,
        origin=origin,
        ctx=DEFAULT_CONTEXT,
    )
    assert child == "run-1"
    assert runs.submitted["pipeline_origin"] is origin
    result = executor.result("run-1", ctx=DEFAULT_CONTEXT)
    assert result is not None and result.status is NodeStatus.COMPLETED
    executor.cancel("run-1", ctx=DEFAULT_CONTEXT)
    assert runs.intervened == ("run-1", InterventionAction.STOP)


def test_run_node_executor_maps_running_and_failed() -> None:
    runs = _FakeRuns(_run(RunState.RUNNING))
    assert RunNodeExecutor(runs).result("run-1", ctx=DEFAULT_CONTEXT) is None
    runs.run = _run(RunState.FAILED, failure_reason="boom", cost_usd=0.1)
    failed = RunNodeExecutor(runs).result("run-1", ctx=DEFAULT_CONTEXT)
    assert failed is not None and failed.status is NodeStatus.FAILED
    assert failed.error == "boom"


class _FakeApprovals:
    class _Record:
        def __init__(self, approval_id: str, status: str) -> None:
            self.approval_id = approval_id
            self.status = status

    def __init__(self) -> None:
        self._records: dict[str, Any] = {}

    def request(self, *, run_id: str, workload: str, rule: str, reason: str) -> Any:
        record = self._Record(f"appr-{len(self._records) + 1}", "pending")
        self._records[record.approval_id] = record
        return record

    def get(self, approval_id: str) -> Any:
        return self._records[approval_id]


def test_service_approval_gate() -> None:
    approvals = _FakeApprovals()
    gate = ServiceApprovalGate(approvals)
    approval_id = gate.request(run_id="pr-1", workload="a", rule="r", reason="x")
    assert approval_id == "appr-1"
    assert gate.status(approval_id) == "pending"


# --------------------------------------------------------------------------- #
# Handoff / store edge paths
# --------------------------------------------------------------------------- #
def test_validate_schema_all_scalar_types() -> None:
    validate_schema(["a"], {"type": "array", "items": {"type": "string"}})
    validate_schema(3, {"type": "integer"})
    validate_schema(3.5, {"type": "number"})
    validate_schema(True, {"type": "boolean"})
    validate_schema(None, {"type": "null"})
    with pytest.raises(HandoffError):
        validate_schema("x", {"type": "array"})
    with pytest.raises(HandoffError):
        validate_schema(True, {"type": "integer"})
    with pytest.raises(HandoffError):
        validate_schema(1, {"type": "null"})


def test_render_ref_stringifies_bool_none_and_rejects_containers() -> None:
    context = PipelineContext(variables={"flag": True, "empty": None, "obj": {"a": 1}})
    assert render_ref("flag=${flag}", context) == "flag=true"
    assert render_ref("empty=${empty}", context) == "empty="
    with pytest.raises(HandoffError):
        render_ref("obj=${obj}", context)


def test_pipeline_context_missing_variable_raises() -> None:
    with pytest.raises(HandoffError):
        PipelineContext().lookup("nope")


def test_postgres_pipeline_store_update_branches(pg_engine: Engine) -> None:
    reset_database(pg_engine)
    store = PostgresPipelineStore(pg_engine)
    from test_pipelines_store import _header, _node, _spec

    store.save_spec(_spec())
    store.save_spec(_spec().model_copy(update={"name": "renamed"}))
    loaded = store.get_spec("incident-response")
    assert loaded is not None and loaded.name == "renamed"

    store.save_run(_header())
    store.save_run(_header(PipelineState.COMPLETED))
    assert store.get_run("pr-1").state is PipelineState.COMPLETED  # type: ignore[union-attr]

    store.save_node_run(_node(1))
    store.save_node_run(_node(1, NodeStatus.COMPLETED))
    node = store.get_node_run("pr-1", "triage", 1)
    assert node is not None and node.status is NodeStatus.COMPLETED

    store.clear()
    assert store.list_specs() == []


def test_pipeline_spec_rejects_unknown_edge_and_duplicate() -> None:
    with pytest.raises(ValidationError):
        PipelineSpec.model_validate(
            {
                "id": "p",
                "name": "p",
                "nodes": [
                    {"id": "a", "kind": "workload", "workload": "a"},
                    {"id": "a", "kind": "workload", "workload": "b"},
                ],
            }
        )
