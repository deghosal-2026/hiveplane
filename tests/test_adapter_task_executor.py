"""Tests for the AdapterTaskExecutor bridge (M23, #117)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from itertools import count
from typing import Any

import pytest

from hiveplane.adapters.base import AdapterRunExecutor
from hiveplane.adapters.raw_worker import RawWorkerAdapter
from hiveplane.adapters.worker import WorkerContext
from hiveplane.certification.executor import AdapterTaskExecutor, MissingPinnedModelError
from hiveplane.certification.models import (
    BenchmarkCorpus,
    BenchmarkTask,
    BenchmarkTaskCheck,
    CheckStatus,
    CheckType,
    Environment,
)
from hiveplane.certification.runner import BenchmarkRunner, TaskExecution
from hiveplane.core.decision import DecisionOutcome
from hiveplane.core.run import Run
from hiveplane.core.tools import ToolTrustLevel
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.admission import AdmissionPipeline
from hiveplane.execution.gates import NullRunExecutor
from hiveplane.execution.service import RunService
from hiveplane.execution.store import InMemoryRunStore
from hiveplane.execution.tools import ToolGateway
from hiveplane.policy.engine import PolicyEngine
from hiveplane.policy.packs import InMemoryPolicyPackStore
from hiveplane.registry.models import ToolRecord
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore
from test_execution_admission import _Budget, _Cert, _Policy, _Sandbox

_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_MODEL = "openai/gpt-4o/2024-08-06"
_ENV = Environment(
    sandbox_image="hiveplane/sandbox:0.1.0",
    runtime_adapter="raw-worker",
    control_plane_version="0.1.0",
)


def _clock() -> datetime:
    return _NOW


class _FanOut:
    def notify(self, run: Run, workload: AgentWorkload) -> list[Any]:
        return []

    def notify_escalation(self, run: Run, workload: AgentWorkload) -> list[Any]:
        return []


class _Loader:
    def __init__(self, entry: Callable[..., object]) -> None:
        self._entry = entry

    def load(self, entrypoint: str) -> Callable[..., object]:
        return self._entry


def _service(
    make_manifest: Callable[..., AgentWorkload], entry: Callable[..., object]
) -> tuple[RunService, RegistryService]:
    registry = RegistryService(InMemoryRegistryStore())
    registry.register_tool(
        ToolRecord(
            tool_id="mcp.t.read",
            name="read",
            mcp_server="test",
            trust_level=ToolTrustLevel.READ_ONLY,
            registered_at=_NOW,
            registered_by="test",
        )
    )
    workload = make_manifest(
        name="agent-1",
        tools={"allow": [{"tool_id": "mcp.t.read", "trust_level": "read_only"}]},
    )
    registry.create(workload)
    ids = count(1)
    admission = AdmissionPipeline(
        _Cert(True, None),
        _Policy(DecisionOutcome.ALLOW),
        _Budget(True),
        _Sandbox(False),
        clock=_clock,
    )
    service = RunService(
        InMemoryRunStore(),
        registry,
        admission=admission,
        executor=NullRunExecutor(),
        fanout=_FanOut(),
        clock=_clock,
        id_factory=lambda: f"run-{next(ids)}",
    )
    gateway = ToolGateway(
        registry,
        PolicyEngine(InMemoryPolicyPackStore(), clock=_clock),
        service,
        clock=_clock,
    )
    adapter = RawWorkerAdapter(
        service,
        gateway,
        _Loader(entry),  # type: ignore[arg-type]
        clock=_clock,
        spawner=lambda work: work(),
    )
    service.attach_executor(AdapterRunExecutor(adapter))
    return service, registry


def _task() -> BenchmarkTask:
    return BenchmarkTask(
        id="t1",
        name="classify low risk",
        input={"pr": {"title": "Fix typo in README"}},
        check=BenchmarkTaskCheck(type=CheckType.EXACT_MATCH, field="risk", value="low"),
    )


def _good_agent(task: dict[str, Any], ctx: WorkerContext) -> dict[str, Any]:
    ctx.tool_call("mcp.t.read")
    ctx.report_usage(input_tokens=10, output_tokens=5)
    return {"risk": "low"}


def _broken_agent(task: dict[str, Any], ctx: WorkerContext) -> dict[str, Any]:
    raise RuntimeError("boom")


def test_executor_runs_a_task_and_collects_the_result(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, registry = _service(make_manifest, _good_agent)
    executor = AdapterTaskExecutor(
        service,
        registry,
        workload="agent-1",
        model_identity=_MODEL,
        sleep=lambda seconds: None,
    )

    execution = executor.execute(_task())

    assert execution.output == {"risk": "low"}
    assert execution.actions == ["mcp.t.read"]
    assert execution.tokens == 15
    assert execution.model_identity == _MODEL


def test_known_good_agent_passes_the_benchmark(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, registry = _service(make_manifest, _good_agent)
    executor = AdapterTaskExecutor(
        service,
        registry,
        workload="agent-1",
        model_identity=_MODEL,
        sleep=lambda seconds: None,
    )
    runner = BenchmarkRunner(
        executor,
        model_identity=_MODEL,
        benchmark_version="1.0.0",
        environment=_ENV,
        clock=_clock,
    )

    result = runner.run(
        BenchmarkCorpus(id="c", version=1, tasks=[_task()]),
        workload_id="agent-1",
        manifest_version=1,
    )

    assert result.tasks[0].status is CheckStatus.PASS
    assert result.aggregate.passed == 1


def test_broken_agent_fails_the_benchmark(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, registry = _service(make_manifest, _broken_agent)
    executor = AdapterTaskExecutor(
        service,
        registry,
        workload="agent-1",
        model_identity=_MODEL,
        sleep=lambda seconds: None,
    )
    runner = BenchmarkRunner(
        executor,
        model_identity=_MODEL,
        benchmark_version="1.0.0",
        environment=_ENV,
        clock=_clock,
    )

    result = runner.run(
        BenchmarkCorpus(id="c", version=1, tasks=[_task()]),
        workload_id="agent-1",
        manifest_version=1,
    )

    assert result.tasks[0].status is CheckStatus.FAIL
    assert result.aggregate.passed == 0


def test_executor_refuses_without_a_pinned_model(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    service, registry = _service(make_manifest, _good_agent)
    executor = AdapterTaskExecutor(
        service,
        registry,
        workload="agent-1",
        model_identity=None,
        sleep=lambda seconds: None,
    )

    with pytest.raises(MissingPinnedModelError):
        executor.execute(_task())


def test_factory_selects_the_configured_executor(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    from hiveplane.certification.executor import build_task_executor
    from hiveplane.certification.runner import (
        ReferenceExecutor,
        UnconfiguredTaskExecutor,
    )
    from hiveplane.config import CertificationSettings, Settings

    service, registry = _service(make_manifest, _good_agent)

    default = build_task_executor(Settings(), service, registry)
    reference = build_task_executor(
        Settings(certification=CertificationSettings(executor="reference")),
        service,
        registry,
    )

    assert isinstance(default, UnconfiguredTaskExecutor)
    assert isinstance(reference, ReferenceExecutor)


class _WrongModelExecutor:
    def execute(self, task: BenchmarkTask) -> TaskExecution:
        return TaskExecution(
            output={"risk": "low"},
            latency_ms=1,
            model_identity="other/model/1",
        )


def test_runner_rejects_an_executed_model_mismatch() -> None:
    runner = BenchmarkRunner(
        _WrongModelExecutor(),
        model_identity=_MODEL,
        benchmark_version="1.0.0",
        environment=_ENV,
        clock=_clock,
    )

    result = runner.run(
        BenchmarkCorpus(id="c", version=1, tasks=[_task()]),
        workload_id="agent-1",
        manifest_version=1,
    )

    assert result.tasks[0].status is CheckStatus.FAIL
    assert result.tasks[0].failure_reason is not None
    assert "model_identity" in result.tasks[0].failure_reason
