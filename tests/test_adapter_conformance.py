"""Conformance suite: every adapter must behave identically (M17, #45)."""

from __future__ import annotations

import importlib
import sys
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

import pytest

from conformance import Harness, assert_adapter_conforms
from hiveplane.adapters.base import AdapterRunExecutor
from hiveplane.adapters.langgraph import LangGraphAdapter
from hiveplane.adapters.loader import EntrypointLoader
from hiveplane.budget.pricing import CostTable
from hiveplane.budget.service import BudgetService
from hiveplane.budget.store import InMemoryBudgetStore
from hiveplane.core.run import Run, RunState
from hiveplane.core.tools import ToolTrustLevel
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.admission import AdmissionPipeline
from hiveplane.execution.models import DeliveryRecord
from hiveplane.execution.service import RunService
from hiveplane.execution.store import InMemoryRunStore
from hiveplane.execution.wiring import attach_raw_worker, build_tool_gateway
from hiveplane.policy.engine import PolicyEngine
from hiveplane.policy.packs import InMemoryPolicyPackStore
from hiveplane.registry.models import ToolRecord
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore
from test_execution_admission import _Cert, _Sandbox

_MODEL = "openai/gpt-4o/2024-08-06"
_COUNTER = {"n": 0}

_RAW_SCENARIO = '''
import threading

_HELD: dict[str, threading.Event] = {}
_RELEASE: dict[str, threading.Event] = {}


def held(run_id):
    return _HELD.setdefault(run_id, threading.Event())


def release(run_id):
    _RELEASE.setdefault(run_id, threading.Event()).set()


def run(task, ctx):
    ctx.tool_call("mcp.t.read", host="api.example.com", output="payload")
    ctx.report_usage(input_tokens=100, output_tokens=50)
    if task.get("hold"):
        _HELD.setdefault(ctx.run_id, threading.Event()).set()
        _RELEASE.setdefault(ctx.run_id, threading.Event()).wait(10.0)
        ctx.checkpoint()
    return {"ok": True}
'''

_LG_SCENARIO = '''
from typing import TypedDict
import threading

from langchain_core.runnables import RunnableConfig
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.graph import END, START, StateGraph

_HELD: dict[str, threading.Event] = {}
_RELEASE: dict[str, threading.Event] = {}


def held(run_id):
    return _HELD.setdefault(run_id, threading.Event())


def release(run_id):
    _RELEASE.setdefault(run_id, threading.Event()).set()


class S(TypedDict, total=False):
    hold: bool
    done: bool


def work(state: S, config: RunnableConfig) -> S:
    ctx = config["configurable"]["hiveplane_ctx"]
    ctx.tool_call("mcp.t.read", host="api.example.com", output="payload")
    ctx.report_usage(input_tokens=100, output_tokens=50)
    if state.get("hold"):
        _HELD.setdefault(ctx.run_id, threading.Event()).set()
        _RELEASE.setdefault(ctx.run_id, threading.Event()).wait(10.0)
        ctx.checkpoint()
    return {"done": True}


b = StateGraph(S)
b.add_node("work", work)
b.add_edge(START, "work")
b.add_edge("work", END)
graph = b.compile(checkpointer=InMemorySaver())
'''


class _FanOut:
    def notify(self, run: Run, workload: AgentWorkload) -> list[DeliveryRecord]:
        return []

    def notify_escalation(self, run: Run, workload: AgentWorkload) -> list[DeliveryRecord]:
        return []


def _workload(
    make_manifest: Callable[..., AgentWorkload], entrypoint: str, adapter: str = "raw-worker"
) -> AgentWorkload:
    return make_manifest(
        name="agent-1",
        runtime={"adapter": adapter, "entrypoint": entrypoint},
        status="provisional",
        tools={
            "allow": [{"tool_id": "mcp.t.read", "trust_level": "read_only"}],
            "deny": ["mcp.t.denied"],
        },
        output_shaping={"max_bytes": 128, "truncate_strategy": "head"},
        sandbox={
            "enabled": True,
            "resource_caps": {"memory_mb": 128, "cpu_cores": 1.0, "wall_clock_s": 10},
            "egress": {"allow": ["api.example.com"], "mode": "restricted"},
        },
        budget={"per_run_usd": 5.0, "per_day_usd": 50.0, "per_team_usd": 500.0},
    )


def _service(workload: AgentWorkload) -> tuple[RunService, RegistryService, PolicyEngine]:
    registry = RegistryService(InMemoryRegistryStore())
    registry.register_tool(
        ToolRecord(
            tool_id="mcp.t.read",
            name="mcp.t.read",
            mcp_server="srv",
            trust_level=ToolTrustLevel.READ_ONLY,
            registered_at=datetime(2026, 1, 1, tzinfo=UTC),
            registered_by="conformance",
        )
    )
    registry.create(workload)
    policy = PolicyEngine(InMemoryPolicyPackStore())
    budget = BudgetService(InMemoryBudgetStore(), CostTable())
    service = RunService(
        InMemoryRunStore(),
        registry,
        admission=AdmissionPipeline(_Cert(True), policy, budget, _Sandbox(True)),
        executor=None,
        fanout=_FanOut(),
        budget=budget,
    )
    return service, registry, policy


def _import_scenario(tmp_path: Path, base: str, source: str) -> tuple[object, str]:
    _COUNTER["n"] += 1
    module_name = f"{base}_{_COUNTER['n']}"
    (tmp_path / f"{module_name}.py").write_text(source, encoding="utf-8")
    sys.path.insert(0, str(tmp_path))
    try:
        module = importlib.import_module(module_name)
    finally:
        sys.path.remove(str(tmp_path))
    return module, module_name


def _raw_harness(tmp_path: Path, make_manifest: Callable[..., AgentWorkload]) -> Harness:
    module, name = _import_scenario(tmp_path, "conformance_raw", _RAW_SCENARIO)
    workload = _workload(make_manifest, f"{name}:run")
    service, registry, policy = _service(workload)
    gateway = build_tool_gateway(registry, policy, service, approvals=None)
    adapter = attach_raw_worker(service, gateway, root=tmp_path, spawner=None)
    return Harness(
        adapter=adapter,
        service=service,
        workload=workload.name,
        model_identity=_MODEL,
        held=module.held,  # type: ignore[attr-defined]
        release=module.release,  # type: ignore[attr-defined]
    )


def _langgraph_harness(tmp_path: Path, make_manifest: Callable[..., AgentWorkload]) -> Harness:
    module, name = _import_scenario(tmp_path, "conformance_lg", _LG_SCENARIO)
    workload = _workload(make_manifest, f"{name}:graph", adapter="langgraph")
    service, registry, policy = _service(workload)
    gateway = build_tool_gateway(registry, policy, service, approvals=None)
    adapter = LangGraphAdapter(service, gateway, EntrypointLoader(root=tmp_path))
    service.attach_executor(AdapterRunExecutor(adapter))
    return Harness(
        adapter=adapter,
        service=service,
        workload=workload.name,
        model_identity=_MODEL,
        held=module.held,  # type: ignore[attr-defined]
        release=module.release,  # type: ignore[attr-defined]
    )


def test_raw_worker_conforms(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    assert_adapter_conforms(_raw_harness(tmp_path, make_manifest))


def test_langgraph_conforms(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    pytest.importorskip("langgraph")
    assert_adapter_conforms(_langgraph_harness(tmp_path, make_manifest))


class _DeadAdapter:
    """An adapter whose runs never transition; conformance must reject it."""

    def register(self, workload: object) -> None:
        return None

    def submit(self, context: object) -> None:
        return None

    def pause(self, run_id: str) -> bool:
        return True

    def resume(self, run_id: str) -> bool:
        return True

    def cancel(self, run_id: str) -> None:
        return None

    def status(self, run_id: str) -> RunState:
        return RunState.RUNNING

    def usage(self, run_id: str) -> None:
        return None

    def tool_calls(self, run_id: str) -> list[object]:
        return []


def test_broken_adapter_fails_the_suite(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    harness = _raw_harness(tmp_path, make_manifest)
    harness.adapter = _DeadAdapter()  # type: ignore[assignment]
    with pytest.raises(AssertionError):
        assert_adapter_conforms(harness)
