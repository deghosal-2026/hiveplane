"""Tests for adapter wiring."""

from __future__ import annotations

from collections.abc import Callable
from pathlib import Path

import pytest

from hiveplane.adapters.langgraph import LangGraphAdapter
from hiveplane.adapters.raw_worker import RawWorkerAdapter
from hiveplane.api.app import create_app
from hiveplane.budget.pricing import CostTable
from hiveplane.budget.service import BudgetService
from hiveplane.budget.store import InMemoryBudgetStore
from hiveplane.config import Settings, get_settings
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.admission import AdmissionPipeline
from hiveplane.execution.models import DeliveryRecord
from hiveplane.execution.service import RunService
from hiveplane.execution.store import InMemoryRunStore
from hiveplane.execution.wiring import attach_langgraph, attach_raw_worker, build_tool_gateway
from hiveplane.policy.engine import PolicyEngine
from hiveplane.policy.packs import InMemoryPolicyPackStore
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore
from test_execution_admission import _Cert, _Sandbox


def test_entrypoints_root_default() -> None:
    assert Settings().execution.entrypoints_root == "."


def test_adapter_is_off_by_default() -> None:
    assert Settings().execution.adapter == "none"
    assert create_app().state.adapter is None


def test_disabled_adapter_warns_at_startup(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING", logger="hiveplane.api.app"):
        create_app()

    assert any(
        "execution.adapter is 'none'" in record.message for record in caplog.records
    )


def test_unconfigured_certification_executor_warns_at_startup(
    caplog: pytest.LogCaptureFixture,
) -> None:
    with caplog.at_level("WARNING", logger="hiveplane.api.app"):
        create_app()

    assert any(
        "certification.executor is 'none'" in record.message for record in caplog.records
    )


def test_adapter_is_enabled_by_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HIVEPLANE_EXECUTION__ADAPTER", "raw-worker")
    get_settings.cache_clear()
    app = create_app()
    assert isinstance(app.state.adapter, RawWorkerAdapter)


def test_langgraph_adapter_is_enabled_by_config(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HIVEPLANE_EXECUTION__ADAPTER", "langgraph")
    get_settings.cache_clear()
    app = create_app()
    assert isinstance(app.state.adapter, LangGraphAdapter)


class _FanOut:
    def notify(self, run: Run, workload: AgentWorkload) -> list[DeliveryRecord]:
        return []

    def notify_escalation(self, run: Run, workload: AgentWorkload) -> list[DeliveryRecord]:
        return []


def _service(workload: AgentWorkload) -> tuple[RunService, RegistryService, PolicyEngine]:
    registry = RegistryService(InMemoryRegistryStore())
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


def test_attach_raw_worker_completes_a_run(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    (tmp_path / "wiring_worker.py").write_text(
        "def run(task, ctx):\n"
        "    ctx.report_usage(input_tokens=100, output_tokens=50, cost_usd=0.0)\n"
        "    return {'ok': True}\n",
        encoding="utf-8",
    )
    workload = make_manifest(
        name="agent-1",
        runtime={"adapter": "raw-worker", "entrypoint": "wiring_worker:run"},
    )
    service, registry, policy = _service(workload)
    gateway = build_tool_gateway(registry, policy, service, approvals=None)
    adapter = attach_raw_worker(
        service, gateway, root=tmp_path, spawner=lambda work: work()
    )
    assert isinstance(adapter, RawWorkerAdapter)

    run = service.submit(
        workload="agent-1",
        caller="cli",
        context=AdmissionContext.SANDBOX,
        model_identity="openai/gpt-4o/2024-08-06",
    )
    service.start(run.id, actor="cli")
    completed = service.get(run.id)
    assert completed.state is RunState.COMPLETED
    assert completed.result == {"ok": True}
    assert completed.cost_usd > 0.0


def test_attach_langgraph_completes_a_run(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    (tmp_path / "stub_graph.py").write_text(
        "class _Graph:\n"
        "    def stream(self, payload, config, *, stream_mode='values'):\n"
        "        yield {'state': 'done'}\n"
        "    def get_state(self, config):\n"
        "        return type('Snap', (), {'next': (), 'values': {'out': 'ok'}})()\n"
        "graph = _Graph()\n",
        encoding="utf-8",
    )
    workload = make_manifest(
        name="agent-1",
        runtime={"adapter": "langgraph", "entrypoint": "stub_graph:graph"},
    )
    service, registry, policy = _service(workload)
    gateway = build_tool_gateway(registry, policy, service, approvals=None)
    adapter = attach_langgraph(service, gateway, root=tmp_path, spawner=lambda work: work())
    assert isinstance(adapter, LangGraphAdapter)

    run = service.submit(
        workload="agent-1",
        caller="cli",
        context=AdmissionContext.SANDBOX,
        model_identity="openai/gpt-4o/2024-08-06",
    )
    service.start(run.id, actor="cli")
    completed = service.get(run.id)
    assert completed.state is RunState.COMPLETED
    assert completed.result == {"out": "ok"}
