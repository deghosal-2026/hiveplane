"""Tests for approval re-dispatch on resume (M23, #129).

When a destructive tool call escalates, the run pauses. On approval + resume
the escalated call must actually execute (against the fixture executor) and the
agent must complete — approving alone must not drop the tool call.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from hiveplane.adapters.base import AdapterRunExecutor
from hiveplane.adapters.langgraph import LangGraphAdapter
from hiveplane.adapters.loader import EntrypointLoader
from hiveplane.budget.pricing import CostTable
from hiveplane.budget.service import BudgetService
from hiveplane.budget.store import InMemoryBudgetStore
from hiveplane.core.approval import ApprovalStatus
from hiveplane.core.decision import DecisionOutcome
from hiveplane.core.event import EventType
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.tools import ToolTrustLevel
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.admission import AdmissionPipeline
from hiveplane.execution.models import DeliveryRecord, InterventionAction
from hiveplane.execution.service import RunService
from hiveplane.execution.store import InMemoryRunStore
from hiveplane.execution.tools import ToolCallOutcome, ToolCallRequest
from hiveplane.execution.wiring import attach_raw_worker, build_tool_gateway
from hiveplane.policy.approvals import ApprovalService
from hiveplane.policy.store import InMemoryApprovalStore
from hiveplane.registry.models import ToolRecord
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore
from test_execution_admission import _Cert, _Policy, _Sandbox

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_MODEL = "openai/gpt-4o/2024-08-06"


class _FanOut:
    def notify(self, run: Run, workload: AgentWorkload) -> list[DeliveryRecord]:
        return []

    def notify_escalation(self, run: Run, workload: AgentWorkload) -> list[DeliveryRecord]:
        return []


def _harness(
    make_manifest: Callable[..., AgentWorkload],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
    *,
    entrypoint: str = "examples.worker:run",
    adapter: str = "raw-worker",
) -> tuple[RunService, RegistryService, ApprovalService]:
    tools = tmp_path / "tools"
    tools.mkdir(parents=True, exist_ok=True)
    (tools / "mcp.t.write.json").write_text('{"status": "ok"}', encoding="utf-8")
    monkeypatch.setenv("HIVEPLANE_EXECUTION__TOOL_FIXTURES", str(tools))
    registry = RegistryService(InMemoryRegistryStore())
    registry.register_tool(
        ToolRecord(
            tool_id="mcp.t.write",
            name="write",
            mcp_server="test",
            trust_level=ToolTrustLevel.DESTRUCTIVE,
            registered_at=_NOW,
            registered_by="test",
        )
    )
    workload = make_manifest(
        name="agent-1",
        runtime={"adapter": adapter, "entrypoint": entrypoint},
        tools={
            "allow": [
                {"tool_id": "mcp.t.write", "trust_level": "destructive", "require_approval": True}
            ]
        },
        approvals={"required_for": ["destructive"]},
    )
    registry.create(workload)
    approvals = ApprovalService(InMemoryApprovalStore())
    budget = BudgetService(InMemoryBudgetStore(), CostTable())
    service = RunService(
        InMemoryRunStore(),
        registry,
        # Admission ALLOWs the run; the *tool* gateway escalates destructive calls.
        admission=AdmissionPipeline(
            _Cert(True), _Policy(DecisionOutcome.ALLOW), budget, _Sandbox(True)
        ),
        executor=None,
        fanout=_FanOut(),
        budget=budget,
    )
    return service, registry, approvals


def test_gateway_allows_a_second_call_once_approved(
    make_manifest: Callable[..., AgentWorkload],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    service, registry, approvals = _harness(make_manifest, monkeypatch, tmp_path)
    gateway = build_tool_gateway(registry, _Policy(DecisionOutcome.ESCALATE), service, approvals)
    run = service.submit(
        workload="agent-1", caller="cli", context=AdmissionContext.SANDBOX, model_identity=_MODEL
    )

    first = gateway.invoke(run.id, ToolCallRequest(tool_id="mcp.t.write"))
    assert first.outcome is ToolCallOutcome.ESCALATED

    pending = approvals.list(run_id=run.id)
    assert pending and pending[0].status is ApprovalStatus.PENDING
    approvals.decide(pending[0].approval_id, status=ApprovalStatus.APPROVED, operator="op")

    second = gateway.invoke(run.id, ToolCallRequest(tool_id="mcp.t.write"))
    assert second.outcome is ToolCallOutcome.ALLOWED
    assert second.approval_id == pending[0].approval_id


def test_raw_worker_completes_after_approval_and_resume(
    make_manifest: Callable[..., AgentWorkload],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "approval_worker.py").write_text(
        "from hiveplane.core.decision import ActionClass\n"
        "def run(task, ctx):\n"
        "    ctx.tool_call('mcp.t.write', action_class=ActionClass.DESTRUCTIVE)\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    service, registry, approvals = _harness(
        make_manifest, monkeypatch, tmp_path, entrypoint="approval_worker:run"
    )
    gateway = build_tool_gateway(registry, _Policy(DecisionOutcome.ESCALATE), service, approvals)
    attach_raw_worker(service, gateway, root=tmp_path, spawner=lambda work: work())

    run = service.submit(
        workload="agent-1", caller="cli", context=AdmissionContext.SANDBOX, model_identity=_MODEL
    )
    service.start(run.id, actor="cli")
    assert service.get(run.id).state is RunState.PAUSED

    pending = approvals.list(run_id=run.id)
    approvals.decide(pending[0].approval_id, status=ApprovalStatus.APPROVED, operator="op")
    service.intervene(run.id, InterventionAction.RESUME, actor="op")

    completed = service.get(run.id)
    assert completed.state is RunState.COMPLETED
    assert completed.result == {"ok": True}
    tool_calls = [
        event.detail
        for event in service.events(run.id)
        if event.type is EventType.TOOL_CALL and event.detail is not None
    ]
    assert tool_calls == ["mcp.t.write"]


def test_langgraph_completes_after_approval_and_resume(
    make_manifest: Callable[..., AgentWorkload],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "approval_graph.py").write_text(
        "from hiveplane.core.decision import ActionClass\n"
        "_calls = []\n"
        "class _Graph:\n"
        "    def stream(self, payload, config, *, stream_mode='values'):\n"
        "        _calls.append(1)\n"
        "        if len(_calls) == 1:\n"
        "            ctx = config['configurable']['hiveplane_ctx']\n"
        "            ctx.tool_call('mcp.t.write', action_class=ActionClass.DESTRUCTIVE)\n"
        "            yield {}\n"
        "        else:\n"
        "            yield {'result': {'ok': True}}\n"
        "    def get_state(self, config):\n"
        "        return type('S', (), {'next': (), 'values': {'result': {'ok': True}}})()\n"
        "graph = _Graph()\n",
        encoding="utf-8",
    )
    service, registry, approvals = _harness(
        make_manifest,
        monkeypatch,
        tmp_path,
        entrypoint="approval_graph:graph",
        adapter="langgraph",
    )
    gateway = build_tool_gateway(registry, _Policy(DecisionOutcome.ESCALATE), service, approvals)
    adapter = LangGraphAdapter(
        service, gateway, EntrypointLoader(root=tmp_path), spawner=lambda work: work()
    )
    service.attach_executor(AdapterRunExecutor(adapter))

    run = service.submit(
        workload="agent-1", caller="cli", context=AdmissionContext.SANDBOX, model_identity=_MODEL
    )
    service.start(run.id, actor="cli")
    assert service.get(run.id).state is RunState.PAUSED

    pending = approvals.list(run_id=run.id)
    approvals.decide(pending[0].approval_id, status=ApprovalStatus.APPROVED, operator="op")
    service.intervene(run.id, InterventionAction.RESUME, actor="op")

    completed = service.get(run.id)
    assert completed.state is RunState.COMPLETED
    assert completed.result == {"result": {"ok": True}}
