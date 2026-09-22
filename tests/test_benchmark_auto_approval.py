"""Tests for benchmark auto-approval of its own pauses (M23, #109, D20).

Certification runs in the sandbox context; a paused run must resolve
deterministically so the benchmark reaches a terminal state. The executor
approves its own pending approvals and resumes, which also clears LangGraph
review-gate interrupts.
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
from hiveplane.certification.executor import AdapterTaskExecutor
from hiveplane.certification.models import BenchmarkTask, BenchmarkTaskCheck, CheckType
from hiveplane.core.approval import ApprovalStatus
from hiveplane.core.decision import DecisionOutcome
from hiveplane.core.run import Run
from hiveplane.core.tools import ToolTrustLevel
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.admission import AdmissionPipeline
from hiveplane.execution.models import DeliveryRecord
from hiveplane.execution.service import RunService
from hiveplane.execution.store import InMemoryRunStore
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
    entrypoint: str,
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
                {
                    "tool_id": "mcp.t.write",
                    "trust_level": "destructive",
                    "require_approval": True,
                }
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
        admission=AdmissionPipeline(
            _Cert(True), _Policy(DecisionOutcome.ALLOW), budget, _Sandbox(True)
        ),
        executor=None,
        fanout=_FanOut(),
        approvals=approvals,
        budget=budget,
    )
    return service, registry, approvals


def _task() -> BenchmarkTask:
    return BenchmarkTask(
        id="t1",
        name="destructive then complete",
        input={},
        check=BenchmarkTaskCheck(type=CheckType.EXACT_MATCH, field="ok", value=True),
    )


def test_executor_auto_approves_a_destructive_escalation(
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
    executor = AdapterTaskExecutor(
        service,
        registry,
        workload="agent-1",
        model_identity=_MODEL,
        approvals=approvals,
        sleep=lambda seconds: None,
    )

    execution = executor.execute(_task())

    assert execution.output == {"ok": True}
    records = approvals.list()
    assert records and records[0].status is ApprovalStatus.APPROVED
    assert records[0].decided_by == "benchmark"


def test_executor_resumes_a_graph_review_interrupt(
    make_manifest: Callable[..., AgentWorkload],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    (tmp_path / "review_graph.py").write_text(
        "_calls = []\n"
        "class _Graph:\n"
        "    def stream(self, payload, config, *, stream_mode='values'):\n"
        "        _calls.append(1)\n"
        "        if len(_calls) == 1:\n"
        "            yield {'__interrupt__': [{'stage': 'review'}]}\n"
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
        entrypoint="review_graph:graph",
        adapter="langgraph",
    )
    gateway = build_tool_gateway(registry, _Policy(DecisionOutcome.ALLOW), service, approvals)
    adapter = LangGraphAdapter(
        service, gateway, EntrypointLoader(root=tmp_path), spawner=lambda work: work()
    )
    service.attach_executor(AdapterRunExecutor(adapter))
    executor = AdapterTaskExecutor(
        service,
        registry,
        workload="agent-1",
        model_identity=_MODEL,
        approvals=approvals,
        sleep=lambda seconds: None,
    )

    execution = executor.execute(_task())

    assert execution.output == {"result": {"ok": True}}
    assert approvals.list() == []
