"""Tests for startup LLM-provider wiring (M23, #136).

The control plane must build the configured provider once at startup, hand it
to both adapters, and fail fast on misconfiguration instead of surfacing the
error mid-run inside a worker thread.
"""

from __future__ import annotations

import json
from collections.abc import Callable
from pathlib import Path

import pytest

from hiveplane.adapters.langgraph import LangGraphAdapter
from hiveplane.adapters.raw_worker import RawWorkerAdapter
from hiveplane.api.app import create_app
from hiveplane.budget.pricing import CostTable
from hiveplane.budget.service import BudgetService
from hiveplane.budget.store import InMemoryBudgetStore
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.admission import AdmissionPipeline
from hiveplane.execution.models import DeliveryRecord
from hiveplane.execution.service import RunService
from hiveplane.execution.store import InMemoryRunStore
from hiveplane.execution.wiring import attach_raw_worker, build_tool_gateway
from hiveplane.llm.fake import FakeProvider, replay_key
from hiveplane.llm.models import CompletionRequest, Message
from hiveplane.llm.openai import OpenAICompatibleProvider
from hiveplane.policy.engine import PolicyEngine
from hiveplane.policy.packs import InMemoryPolicyPackStore
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore
from test_execution_admission import _Cert, _Sandbox


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


def test_app_state_provider_defaults_to_fake() -> None:
    app = create_app()
    assert isinstance(app.state.provider, FakeProvider)


def test_app_state_provider_selects_cloud_backend(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HIVEPLANE_MODEL__PROVIDER", "cloud")
    monkeypatch.setenv("HIVEPLANE_MODEL__API_KEY", "sk-test")
    app = create_app()
    assert isinstance(app.state.provider, OpenAICompatibleProvider)


def test_local_provider_without_base_url_fails_fast(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HIVEPLANE_MODEL__PROVIDER", "local")
    with pytest.raises(ValueError, match="base_url"):
        create_app()


def test_real_provider_without_aliases_warns_at_startup(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("HIVEPLANE_MODEL__PROVIDER", "cloud")
    monkeypatch.setenv("HIVEPLANE_MODEL__API_KEY", "sk-test")
    monkeypatch.delenv("HIVEPLANE_MODEL__MODEL_ALIASES", raising=False)

    with caplog.at_level("WARNING", logger="hiveplane.api.app"):
        create_app()

    assert any("model_aliases" in record.message for record in caplog.records)


def test_real_provider_with_aliases_does_not_warn(
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    monkeypatch.setenv("HIVEPLANE_MODEL__PROVIDER", "cloud")
    monkeypatch.setenv("HIVEPLANE_MODEL__API_KEY", "sk-test")
    monkeypatch.setenv(
        "HIVEPLANE_MODEL__MODEL_ALIASES",
        '{"gpt-4o-2024-08-06": "openai/gpt-4o/2024-08-06"}',
    )

    with caplog.at_level("WARNING", logger="hiveplane.api.app"):
        create_app()

    assert not any("model_aliases" in record.message for record in caplog.records)


def test_raw_worker_adapter_receives_the_startup_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HIVEPLANE_EXECUTION__ADAPTER", "raw-worker")
    app = create_app()
    adapter = app.state.adapter
    assert isinstance(adapter, RawWorkerAdapter)
    assert adapter._provider is app.state.provider


def test_langgraph_adapter_receives_the_startup_provider(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("HIVEPLANE_EXECUTION__ADAPTER", "langgraph")
    app = create_app()
    adapter = app.state.adapter
    assert isinstance(adapter, LangGraphAdapter)
    assert adapter._provider is app.state.provider


def test_worker_completes_through_the_wired_provider(
    make_manifest: Callable[..., AgentWorkload],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    request = CompletionRequest(
        messages=[Message(role="user", content="ping")],
        model="openai/gpt-4o/2024-08-06",
    )
    replay = tmp_path / "replay.json"
    replay.write_text(json.dumps({replay_key(request): "pong"}), encoding="utf-8")
    monkeypatch.setenv("HIVEPLANE_MODEL__REPLAY_FILE", str(replay))
    (tmp_path / "seam_worker.py").write_text(
        "def run(task, ctx):\n"
        "    return {'reply': ctx.complete('ping').content}\n",
        encoding="utf-8",
    )
    workload = make_manifest(
        name="agent-1",
        runtime={"adapter": "raw-worker", "entrypoint": "seam_worker:run"},
    )
    service, registry, policy = _service(workload)
    gateway = build_tool_gateway(registry, policy, service, approvals=None)
    attach_raw_worker(service, gateway, root=tmp_path, spawner=lambda work: work())

    run = service.submit(
        workload="agent-1",
        caller="cli",
        context=AdmissionContext.SANDBOX,
        model_identity="openai/gpt-4o/2024-08-06",
    )
    service.start(run.id, actor="cli")
    completed = service.get(run.id)

    assert completed.state is RunState.COMPLETED
    assert completed.result == {"reply": "pong"}
