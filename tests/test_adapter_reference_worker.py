"""Tests for the bundled reference worker entrypoint."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path

from hiveplane.adapters.loader import EntrypointLoader
from hiveplane.adapters.worker import RunControl, WorkerContext
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.usage import UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.tools import ToolCallOutcome, ToolCallResult

_PROJECT_ROOT = Path(__file__).resolve().parents[1]
_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def test_bundled_repo_agent_entrypoint_loads() -> None:
    entry = EntrypointLoader(root=_PROJECT_ROOT).load("examples.repo_agent:run")
    assert callable(entry)


def test_bundled_repo_agent_runs(make_manifest: Callable[..., AgentWorkload]) -> None:
    entry = EntrypointLoader(root=_PROJECT_ROOT).load("examples.repo_agent:run")
    calls: list[ToolCallResult] = []
    usage: list[UsageReport] = []
    run = Run(
        id="run-1",
        workload_id="repo-agent",
        caller="cli",
        state=RunState.RUNNING,
        model_identity="openai/gpt-4o/2024-08-06",
        created_at=_NOW,
        updated_at=_NOW,
        context=AdmissionContext.SANDBOX,
        task={"repo": "hiveplane"},
    )

    class _Tools:
        def invoke(self, run_id: str, request: object) -> ToolCallResult:
            return ToolCallResult(
                run_id=run_id,
                tool_id="mcp.github.list_pull_requests",
                outcome=ToolCallOutcome.ALLOWED,
            )

    class _Reporter:
        def get(self, run_id: str) -> Run:
            return run

        def transition(self, run_id: str, target: RunState, **kwargs: object) -> Run:
            return run

        def record_usage(self, run_id: str, report: UsageReport) -> Run:
            usage.append(report)
            return run

        def record_event(
            self, run_id: str, event_type: object, actor: str, **kwargs: object
        ) -> None:
            return None

    ctx = WorkerContext(
        run=run,
        workload=make_manifest(name="repo-agent"),
        sandbox=True,
        tools=_Tools(),  # type: ignore[arg-type]
        reporter=_Reporter(),
        control=RunControl(),
        tool_calls=calls,
        clock=lambda: _NOW,
    )
    result = entry({"repo": "hiveplane"}, ctx)
    assert isinstance(result, dict)
    assert len(calls) == 1
    assert len(usage) == 1
