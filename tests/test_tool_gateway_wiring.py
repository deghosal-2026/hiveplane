"""Tests for the production tool-gateway wiring (M23, #134).

The composition root must attach a fixture-backed tool executor so live runs
receive real tool data instead of fabricated output.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import pytest

from hiveplane.budget.pricing import CostTable
from hiveplane.budget.service import BudgetService
from hiveplane.budget.store import InMemoryBudgetStore
from hiveplane.config import Settings
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.tools import ToolTrustLevel
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.admission import AdmissionPipeline
from hiveplane.execution.models import DeliveryRecord
from hiveplane.execution.service import RunService
from hiveplane.execution.store import InMemoryRunStore
from hiveplane.execution.tool_executor import ToolFixtureNotFoundError
from hiveplane.execution.tools import ToolCallOutcome, ToolCallRequest
from hiveplane.execution.wiring import attach_raw_worker, build_tool_gateway
from hiveplane.policy.engine import PolicyEngine
from hiveplane.policy.packs import InMemoryPolicyPackStore
from hiveplane.registry.models import ToolRecord
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore
from test_execution_admission import _Cert, _Sandbox

_FIXTURE = '{"repo": "hiveplane", "rows": [{"id": 1}]}'
_NOW = datetime(2026, 1, 1, tzinfo=UTC)


class _FanOut:
    def notify(self, run: Run, workload: AgentWorkload) -> list[DeliveryRecord]:
        return []

    def notify_escalation(self, run: Run, workload: AgentWorkload) -> list[DeliveryRecord]:
        return []


def _service(
    make_manifest: Callable[..., AgentWorkload],
    name: str = "agent-1",
    **spec_overrides: Any,
) -> tuple[RunService, RegistryService, PolicyEngine]:
    workload = make_manifest(
        name=name,
        tools={"allow": [{"tool_id": "mcp.t.read", "trust_level": "read_only"}]},
        **spec_overrides,
    )
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


def _fixtures(tmp_path: Path, files: dict[str, str]) -> Path:
    root = tmp_path / "tools"
    root.mkdir()
    for tool_id, content in files.items():
        (root / f"{tool_id}.json").write_text(content, encoding="utf-8")
    return root


def test_default_tool_fixtures_root() -> None:
    assert Settings().execution.tool_fixtures == "deploy/testdata/tools"


def test_build_tool_gateway_serves_fixture_for_allowed_tool(
    make_manifest: Callable[..., AgentWorkload],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixtures = _fixtures(tmp_path, {"mcp.t.read": _FIXTURE})
    monkeypatch.setenv("HIVEPLANE_EXECUTION__TOOL_FIXTURES", str(fixtures))

    service, registry, policy = _service(
        make_manifest, output_shaping={"max_bytes": 16384}
    )
    gateway = build_tool_gateway(registry, policy, service, approvals=None)

    run = service.submit(
        workload="agent-1",
        caller="test",
        context=AdmissionContext.SANDBOX,
        model_identity="openai/gpt-4o/2024-08-06",
    )
    result = gateway.invoke(run.id, ToolCallRequest(tool_id="mcp.t.read"))

    assert result.outcome is ToolCallOutcome.ALLOWED
    assert result.shaped_output is not None
    assert result.shaped_output.text == _FIXTURE


def test_missing_fixture_fails_loudly(
    make_manifest: Callable[..., AgentWorkload],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixtures = _fixtures(tmp_path, {})
    monkeypatch.setenv("HIVEPLANE_EXECUTION__TOOL_FIXTURES", str(fixtures))

    service, registry, policy = _service(make_manifest)
    gateway = build_tool_gateway(registry, policy, service, approvals=None)

    run = service.submit(
        workload="agent-1",
        caller="test",
        context=AdmissionContext.SANDBOX,
        model_identity="openai/gpt-4o/2024-08-06",
    )
    with pytest.raises(ToolFixtureNotFoundError):
        gateway.invoke(run.id, ToolCallRequest(tool_id="mcp.t.read"))


def test_caller_supplied_output_still_overrides_fixture(
    make_manifest: Callable[..., AgentWorkload],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixtures = _fixtures(tmp_path, {"mcp.t.read": _FIXTURE})
    monkeypatch.setenv("HIVEPLANE_EXECUTION__TOOL_FIXTURES", str(fixtures))

    service, registry, policy = _service(
        make_manifest, output_shaping={"max_bytes": 16384}
    )
    gateway = build_tool_gateway(registry, policy, service, approvals=None)

    run = service.submit(
        workload="agent-1",
        caller="test",
        context=AdmissionContext.SANDBOX,
        model_identity="openai/gpt-4o/2024-08-06",
    )
    result = gateway.invoke(
        run.id, ToolCallRequest(tool_id="mcp.t.read", output="caller-provided")
    )

    assert result.outcome is ToolCallOutcome.ALLOWED
    assert result.shaped_output is not None
    assert result.shaped_output.text == "caller-provided"


def test_agent_run_receives_fixture_data(
    make_manifest: Callable[..., AgentWorkload],
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    fixtures = _fixtures(tmp_path, {"mcp.t.read": _FIXTURE})
    monkeypatch.setenv("HIVEPLANE_EXECUTION__TOOL_FIXTURES", str(fixtures))
    (tmp_path / "fixture_worker.py").write_text(
        "def run(task, ctx):\n"
        "    result = ctx.tool_call('mcp.t.read')\n"
        "    shaped = result.shaped_output\n"
        "    return {'tool_text': shaped.text if shaped is not None else None}\n",
        encoding="utf-8",
    )
    service, registry, policy = _service(
        make_manifest,
        runtime={"adapter": "raw-worker", "entrypoint": "fixture_worker:run"},
        output_shaping={"max_bytes": 16384},
    )
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
    assert completed.result == {"tool_text": _FIXTURE}
