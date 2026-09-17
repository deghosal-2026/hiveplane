"""Tests for the raw-worker adapter."""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import UTC, datetime

import pytest
from pydantic import JsonValue

from hiveplane.adapters.errors import ToolCallEscalatedError, UnsupportedAdapterError
from hiveplane.adapters.raw_worker import RawWorkerAdapter
from hiveplane.adapters.worker import WorkerContext
from hiveplane.core.event import EventType
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.usage import UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.models import RunContext
from hiveplane.execution.tools import ToolCallOutcome, ToolCallResult

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _run(task: dict[str, JsonValue] | None = None) -> Run:
    return Run(
        id="run-1",
        workload_id="agent-1",
        caller="cli",
        state=RunState.QUEUED,
        model_identity="openai/gpt-4o/2024-08-06",
        created_at=_NOW,
        updated_at=_NOW,
        context=AdmissionContext.SANDBOX,
        task=task or {},
    )


class _Tools:
    def invoke(self, run_id: str, request: object) -> ToolCallResult:
        return ToolCallResult(run_id=run_id, tool_id="mcp.t.x", outcome=ToolCallOutcome.ALLOWED)


class _Reporter:
    def __init__(self) -> None:
        self.transitions: list[tuple[RunState, str | None]] = []
        self.events: list[EventType] = []
        self.usage: list[UsageReport] = []

    def get(self, run_id: str) -> Run:
        return _run()

    def transition(
        self,
        run_id: str,
        target: RunState,
        *,
        actor: str,
        detail: str | None = None,
        failure_reason: str | None = None,
        result: object | None = None,
    ) -> Run:
        self.transitions.append((target, failure_reason))
        return _run()

    def record_usage(self, run_id: str, report: UsageReport) -> Run:
        self.usage.append(report)
        return _run()

    def record_event(
        self, run_id: str, event_type: EventType, actor: str, *, detail: str | None = None
    ) -> None:
        self.events.append(event_type)


class _Loader:
    def __init__(self, entry: Callable[..., object]) -> None:
        self._entry = entry

    def load(self, entrypoint: str) -> Callable[..., object]:
        return self._entry


def _adapter(
    entry: Callable[..., object], reporter: _Reporter, *, loader: _Loader | None = None
) -> RawWorkerAdapter:
    return RawWorkerAdapter(
        reporter,
        _Tools(),  # type: ignore[arg-type]
        loader or _Loader(entry),  # type: ignore[arg-type]
        clock=lambda: _NOW,
        spawner=lambda work: work(),
    )


def _context(workload: AgentWorkload, task: dict[str, JsonValue] | None = None) -> RunContext:
    return RunContext(run=_run(task), workload=workload, sandbox=True)


def test_registers_only_raw_worker_manifests(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    adapter = _adapter(lambda task, ctx: {"ok": True}, _Reporter())
    with pytest.raises(UnsupportedAdapterError):
        adapter.register(make_manifest(adapter="langgraph"))


def test_successful_worker_completes_the_run(make_manifest: Callable[..., AgentWorkload]) -> None:
    reporter = _Reporter()
    adapter = _adapter(lambda task, ctx: {"ok": True}, reporter)
    adapter.submit(_context(make_manifest()))
    assert reporter.transitions == [(RunState.COMPLETED, None)]
    assert adapter.status("run-1") is RunState.COMPLETED


def test_worker_usage_is_forwarded(make_manifest: Callable[..., AgentWorkload]) -> None:
    reporter = _Reporter()

    def entry(task: dict[str, JsonValue], ctx: WorkerContext) -> dict[str, JsonValue]:
        ctx.report_usage(input_tokens=10, output_tokens=5, cost_usd=0.0)
        return {"ok": True}

    adapter = _adapter(entry, reporter)
    adapter.submit(_context(make_manifest()))
    assert len(reporter.usage) == 1
    assert reporter.usage[0].input_tokens == 10


def test_worker_exception_fails_the_run(make_manifest: Callable[..., AgentWorkload]) -> None:
    def entry(task: dict[str, JsonValue], ctx: WorkerContext) -> dict[str, JsonValue]:
        raise RuntimeError("boom")

    reporter = _Reporter()
    adapter = _adapter(entry, reporter)
    adapter.submit(_context(make_manifest()))
    assert reporter.transitions == [(RunState.FAILED, "RuntimeError: boom")]
    assert adapter.status("run-1") is RunState.FAILED


def test_tool_call_escalation_stops_without_completing(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    def entry(task: dict[str, JsonValue], ctx: WorkerContext) -> dict[str, JsonValue]:
        raise ToolCallEscalatedError(
            ToolCallResult(run_id="run-1", tool_id="mcp.t.x", outcome=ToolCallOutcome.ESCALATED)
        )

    reporter = _Reporter()
    adapter = _adapter(entry, reporter)
    adapter.submit(_context(make_manifest()))
    assert reporter.transitions == []
    assert EventType.OPERATOR_ACTION in reporter.events


def test_pause_resume_and_cancel_delegate(make_manifest: Callable[..., AgentWorkload]) -> None:
    started = threading.Event()
    release = threading.Event()

    def entry(task: dict[str, JsonValue], ctx: WorkerContext) -> dict[str, JsonValue]:
        started.set()
        release.wait(1.0)
        ctx.checkpoint()
        return {"ok": True}

    adapter = RawWorkerAdapter(
        _Reporter(),
        _Tools(),  # type: ignore[arg-type]
        _Loader(entry),  # type: ignore[arg-type]
        clock=lambda: _NOW,
    )
    adapter.submit(_context(make_manifest()))
    assert started.wait(1.0) is True
    assert adapter.pause("run-1") is True
    assert adapter.status("run-1") is RunState.RUNNING
    assert adapter.resume("run-1") is True
    release.set()
    adapter.cancel("run-1")
    assert adapter.pause("unknown") is False
    assert adapter.usage("run-1") is None
    assert adapter.tool_calls("run-1") == []
