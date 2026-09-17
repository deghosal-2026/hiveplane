"""Tests for the worker context client."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from hiveplane.adapters.errors import (
    RunCancelledError,
    RunTerminatedError,
    ToolCallDeniedError,
    ToolCallEscalatedError,
)
from hiveplane.adapters.worker import RunControl, WorkerContext
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.usage import UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.tools import ToolCallOutcome, ToolCallResult

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _run(state: RunState = RunState.RUNNING) -> Run:
    return Run(
        id="run-1",
        workload_id="agent-1",
        caller="cli",
        state=state,
        model_identity="openai/gpt-4o/2024-08-06",
        created_at=_NOW,
        updated_at=_NOW,
        context=AdmissionContext.SANDBOX,
        task={"repo": "hiveplane"},
    )


class _Tools:
    def __init__(self, outcome: ToolCallOutcome) -> None:
        self._outcome = outcome
        self.requests: list[object] = []

    def invoke(self, run_id: str, request: object) -> ToolCallResult:
        self.requests.append(request)
        return ToolCallResult(run_id=run_id, tool_id="mcp.t.x", outcome=self._outcome)


class _Reporter:
    def __init__(self, state: RunState = RunState.RUNNING) -> None:
        self._state = state
        self.usage: list[UsageReport] = []

    def get(self, run_id: str) -> Run:
        return _run(self._state)

    def transition(self, run_id: str, target: RunState, **kwargs: object) -> Run:
        return _run(target)

    def record_usage(self, run_id: str, report: UsageReport) -> Run:
        self.usage.append(report)
        return _run(self._state)

    def record_event(
        self, run_id: str, event_type: object, actor: str, **kwargs: object
    ) -> None:
        return None


def _context(
    make_manifest: Callable[..., AgentWorkload],
    *,
    tools: _Tools,
    reporter: _Reporter,
    control: RunControl | None = None,
    tool_calls: list[ToolCallResult] | None = None,
) -> WorkerContext:
    return WorkerContext(
        run=_run(),
        workload=make_manifest(),
        sandbox=True,
        tools=tools,  # type: ignore[arg-type]
        reporter=reporter,
        control=control or RunControl(),
        tool_calls=tool_calls if tool_calls is not None else [],
        clock=lambda: _NOW,
    )


def test_context_exposes_run_metadata(make_manifest: Callable[..., AgentWorkload]) -> None:
    ctx = _context(make_manifest, tools=_Tools(ToolCallOutcome.ALLOWED), reporter=_Reporter())
    assert ctx.run_id == "run-1"
    assert ctx.workload == "agent-1"
    assert ctx.sandbox is True
    assert ctx.model_identity == "openai/gpt-4o/2024-08-06"
    assert ctx.task == {"repo": "hiveplane"}


def test_allowed_tool_call_is_recorded(make_manifest: Callable[..., AgentWorkload]) -> None:
    calls: list[ToolCallResult] = []
    ctx = _context(
        make_manifest,
        tools=_Tools(ToolCallOutcome.ALLOWED),
        reporter=_Reporter(),
        tool_calls=calls,
    )
    result = ctx.tool_call("mcp.t.x")
    assert result.outcome is ToolCallOutcome.ALLOWED
    assert calls == [result]


def test_denied_tool_call_raises(make_manifest: Callable[..., AgentWorkload]) -> None:
    ctx = _context(make_manifest, tools=_Tools(ToolCallOutcome.DENIED), reporter=_Reporter())
    with pytest.raises(ToolCallDeniedError):
        ctx.tool_call("mcp.t.x")


def test_escalated_tool_call_raises(make_manifest: Callable[..., AgentWorkload]) -> None:
    ctx = _context(make_manifest, tools=_Tools(ToolCallOutcome.ESCALATED), reporter=_Reporter())
    with pytest.raises(ToolCallEscalatedError):
        ctx.tool_call("mcp.t.x")


def test_report_usage_prices_and_forwards(make_manifest: Callable[..., AgentWorkload]) -> None:
    reporter = _Reporter()
    ctx = _context(make_manifest, tools=_Tools(ToolCallOutcome.ALLOWED), reporter=reporter)
    ctx.report_usage(input_tokens=10, output_tokens=5, tool_calls=1, cost_usd=0.01)
    assert len(reporter.usage) == 1
    assert reporter.usage[0].input_tokens == 10
    assert reporter.usage[0].model_identity == "openai/gpt-4o/2024-08-06"


def test_report_usage_raises_when_terminal(make_manifest: Callable[..., AgentWorkload]) -> None:
    reporter = _Reporter(state=RunState.FAILED)
    ctx = _context(make_manifest, tools=_Tools(ToolCallOutcome.ALLOWED), reporter=reporter)
    with pytest.raises(RunTerminatedError):
        ctx.report_usage(cost_usd=0.01)


def test_checkpoint_raises_when_cancelled(make_manifest: Callable[..., AgentWorkload]) -> None:
    control = RunControl()
    control.cancel()
    ctx = _context(
        make_manifest, tools=_Tools(ToolCallOutcome.ALLOWED), reporter=_Reporter(), control=control
    )
    with pytest.raises(RunCancelledError):
        ctx.checkpoint()
