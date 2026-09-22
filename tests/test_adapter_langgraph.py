"""Tests for the LangGraph adapter (fake graph; no langgraph required)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

import pytest

from hiveplane.adapters.errors import UnsupportedAdapterError
from hiveplane.adapters.langgraph import LangGraphAdapter
from hiveplane.core.event import EventType
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.usage import UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.models import RunContext
from hiveplane.execution.tools import ToolCallOutcome, ToolCallResult

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _run(task: dict[str, Any] | None = None) -> Run:
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


class _Snapshot:
    def __init__(self, next_nodes: tuple[str, ...], values: dict[str, Any]) -> None:
        self._next = next_nodes
        self._values = values

    @property
    def next(self) -> tuple[str, ...]:
        return self._next

    @property
    def values(self) -> dict[str, Any]:
        return self._values


class _Graph:
    def __init__(self, chunks: list[dict[str, Any]], snapshot: _Snapshot) -> None:
        self._chunks = chunks
        self._snapshot = snapshot

    def stream(
        self, payload: Any, config: dict[str, Any], *, stream_mode: str = "values"
    ) -> Iterator[dict[str, Any]]:
        yield from self._chunks

    def get_state(self, config: dict[str, Any]) -> _Snapshot:
        return self._snapshot


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
    def __init__(self, graph: object) -> None:
        self._graph = graph

    def load_object(self, entrypoint: str) -> object:
        return self._graph


def _adapter(graph: object, reporter: _Reporter) -> LangGraphAdapter:
    return LangGraphAdapter(
        reporter,
        _Tools(),  # type: ignore[arg-type]
        _Loader(graph),  # type: ignore[arg-type]
        clock=lambda: _NOW,
        spawner=lambda work: work(),
    )


def _context(workload: AgentWorkload) -> RunContext:
    return RunContext(run=_run(), workload=workload, sandbox=True)


def test_rejects_non_langgraph_manifests(make_manifest: Callable[..., AgentWorkload]) -> None:
    adapter = _adapter(_Graph([], _Snapshot((), {})), _Reporter())
    with pytest.raises(UnsupportedAdapterError):
        adapter.register(make_manifest(adapter="raw-worker"))


def test_completes_a_run_with_final_state(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    graph = _Graph([{"note": "a"}], _Snapshot((), {"note": "done"}))
    reporter = _Reporter()
    adapter = _adapter(graph, reporter)
    adapter.submit(_context(make_manifest(adapter="langgraph")))
    assert reporter.transitions == [(RunState.COMPLETED, None)]
    assert adapter.status("run-1") is RunState.COMPLETED


def test_graph_exception_fails_the_run(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    class _Boom(_Graph):
        def stream(
            self, payload: Any, config: dict[str, Any], *, stream_mode: str = "values"
        ) -> Iterator[dict[str, Any]]:
            raise RuntimeError("kaboom")

    reporter = _Reporter()
    adapter = _adapter(_Boom([], _Snapshot((), {})), reporter)
    adapter.submit(_context(make_manifest(adapter="langgraph")))
    assert reporter.transitions == [(RunState.FAILED, "RuntimeError: kaboom")]


def test_interrupt_pauses_the_run(make_manifest: Callable[..., AgentWorkload]) -> None:
    graph = _Graph(
        [{"note": "a"}, {"__interrupt__": ("approve?",)}],
        _Snapshot(("gate",), {"note": "a"}),
    )
    reporter = _Reporter()
    adapter = _adapter(graph, reporter)
    adapter.submit(_context(make_manifest(adapter="langgraph")))
    assert reporter.transitions == [(RunState.PAUSED, None)]
    assert adapter.status("run-1") is RunState.PAUSED


class _CapturingGraph(_Graph):
    def __init__(self, snapshot: _Snapshot) -> None:
        super().__init__([], snapshot)
        self.payload: object | None = None

    def stream(
        self, payload: object, config: dict[str, Any], *, stream_mode: str = "values"
    ) -> Iterator[dict[str, Any]]:
        self.payload = payload
        yield from ()


def test_submit_passes_the_run_task_wrapped_under_task(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    graph = _CapturingGraph(_Snapshot((), {"result": {"ok": True}}))
    adapter = _adapter(graph, _Reporter())
    workload = make_manifest(adapter="langgraph")
    run = _run(task={"issue": "Document the certification CLI"})
    adapter.submit(RunContext(run=run, workload=workload, sandbox=True))

    assert graph.payload == {"task": {"issue": "Document the certification CLI"}}
