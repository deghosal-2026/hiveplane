"""Tests for fixture-backed tool execution (M23, #116)."""

from __future__ import annotations

import json
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from types import SimpleNamespace

import pytest

from hiveplane.core.event import EventType
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.models import InterventionAction
from hiveplane.execution.tool_executor import (
    FixtureToolExecutor,
    ToolFixtureNotFoundError,
)
from hiveplane.execution.tools import ToolCallOutcome, ToolCallRequest, ToolGateway
from hiveplane.policy.engine import PolicyEngine
from hiveplane.policy.packs import InMemoryPolicyPackStore
from hiveplane.shaping.injection import InjectionScanner
from hiveplane.shaping.pipeline import ShapingPipeline

_FIXED_NOW = datetime(2026, 1, 1, tzinfo=UTC)
_TOOL = "mcp.github.list_pull_requests"


def _write_fixture(root: Path, tool_id: str, payload: object) -> None:
    directory = root / "deploy" / "testdata" / "tools"
    directory.mkdir(parents=True, exist_ok=True)
    (directory / f"{tool_id}.json").write_text(json.dumps(payload), encoding="utf-8")


def test_fixture_is_returned(tmp_path: Path) -> None:
    _write_fixture(tmp_path, _TOOL, [{"number": 7, "title": "Fix typo"}])
    executor = FixtureToolExecutor(root=tmp_path / "deploy" / "testdata" / "tools")

    raw = executor.execute(_TOOL)

    assert json.loads(raw) == [{"number": 7, "title": "Fix typo"}]


def test_missing_fixture_raises(tmp_path: Path) -> None:
    executor = FixtureToolExecutor(root=tmp_path)

    with pytest.raises(ToolFixtureNotFoundError):
        executor.execute("mcp.github.nope")


def test_path_traversal_is_rejected(tmp_path: Path) -> None:
    executor = FixtureToolExecutor(root=tmp_path)

    with pytest.raises(ToolFixtureNotFoundError):
        executor.execute("../../etc/passwd")


def test_shipped_fixtures_exist() -> None:
    root = Path(__file__).resolve().parents[1] / "deploy" / "testdata" / "tools"
    executor = FixtureToolExecutor(root=root)
    for tool_id in (
        "mcp.github.list_pull_requests",
        "mcp.github.read_issue",
        "mcp.github.create_pr_comment",
        "prometheus.query",
        "pagerduty.acknowledge",
        "mcp.github.create_pr",
    ):
        assert json.loads(executor.execute(tool_id)) is not None


class _Runs:
    def __init__(self, run: Run) -> None:
        self._run = run
        self.events: list[tuple[EventType, str, str | None]] = []

    def get(self, run_id: str) -> Run:
        return self._run

    def intervene(self, run_id: str, action: InterventionAction, *, actor: str) -> Run:
        return self._run

    def record_event(
        self, run_id: str, event_type: EventType, actor: str, *, detail: str | None = None
    ) -> None:
        self.events.append((event_type, actor, detail))


def _run() -> Run:
    return Run(
        id="run-1",
        workload_id="agent-1",
        caller="cli",
        state=RunState.RUNNING,
        model_identity="openai/gpt-4o/2024-08-06",
        created_at=_FIXED_NOW,
        updated_at=_FIXED_NOW,
        context=AdmissionContext.SANDBOX,
    )


def _workload(make_manifest: Callable[..., AgentWorkload]) -> AgentWorkload:
    return make_manifest(
        name="agent-1",
        tools={"allow": [{"tool_id": _TOOL, "trust_level": "read_only"}]},
        output_shaping={"max_bytes": 4096, "truncate_strategy": "head"},
    )


def _gateway(
    workload: AgentWorkload, runs: _Runs, executor: FixtureToolExecutor | None
) -> ToolGateway:
    registry = SimpleNamespace(get=lambda name: SimpleNamespace(manifest=workload))
    return ToolGateway(
        registry,  # type: ignore[arg-type]
        PolicyEngine(InMemoryPolicyPackStore(), clock=lambda: _FIXED_NOW),
        runs,
        shaping=ShapingPipeline(InjectionScanner()),
        executor=executor,
        clock=lambda: _FIXED_NOW,
    )


def test_gateway_uses_executor_when_output_is_none(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    _write_fixture(tmp_path, _TOOL, {"pull_requests": [{"number": 7}]})
    executor = FixtureToolExecutor(root=tmp_path / "deploy" / "testdata" / "tools")
    gateway = _gateway(_workload(make_manifest), _Runs(_run()), executor)

    result = gateway.invoke("run-1", ToolCallRequest(tool_id=_TOOL))

    assert result.outcome is ToolCallOutcome.ALLOWED
    assert result.shaped_output is not None
    assert json.loads(result.shaped_output.text) == {"pull_requests": [{"number": 7}]}


def test_gateway_propagates_missing_fixture(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    executor = FixtureToolExecutor(root=tmp_path)
    gateway = _gateway(_workload(make_manifest), _Runs(_run()), executor)

    with pytest.raises(ToolFixtureNotFoundError):
        gateway.invoke("run-1", ToolCallRequest(tool_id=_TOOL))


def test_caller_output_overrides_executor(
    make_manifest: Callable[..., AgentWorkload], tmp_path: Path
) -> None:
    _write_fixture(tmp_path, _TOOL, {"from": "fixture"})
    executor = FixtureToolExecutor(root=tmp_path / "deploy" / "testdata" / "tools")
    gateway = _gateway(_workload(make_manifest), _Runs(_run()), executor)

    result = gateway.invoke("run-1", ToolCallRequest(tool_id=_TOOL, output='{"from": "caller"}'))

    assert result.shaped_output is not None
    assert json.loads(result.shaped_output.text) == {"from": "caller"}
