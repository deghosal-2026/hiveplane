"""Tests for per-workload adapter dispatch (M23, #109)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

import pytest

from hiveplane.adapters.dispatch import DispatchingAdapter
from hiveplane.adapters.errors import UnsupportedAdapterError
from hiveplane.core.run import Run, RunState
from hiveplane.core.spec import RuntimeAdapter
from hiveplane.core.usage import UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.models import RunContext
from hiveplane.execution.tools import ToolCallResult


class _SpyAdapter:
    """Records the adapter calls it receives for assertion."""

    def __init__(self) -> None:
        self.registered: list[str] = []
        self.submitted: list[str] = []
        self.actions: list[tuple[str, str]] = []

    def register(self, workload: AgentWorkload) -> None:
        self.registered.append(workload.name)

    def submit(self, context: RunContext) -> None:
        self.submitted.append(context.run.id)

    def pause(self, run_id: str) -> bool:
        self.actions.append(("pause", run_id))
        return True

    def resume(self, run_id: str) -> bool:
        self.actions.append(("resume", run_id))
        return True

    def cancel(self, run_id: str) -> None:
        self.actions.append(("cancel", run_id))

    def status(self, run_id: str) -> RunState:
        self.actions.append(("status", run_id))
        return RunState.RUNNING

    def usage(self, run_id: str) -> UsageReport | None:
        self.actions.append(("usage", run_id))
        return None

    def tool_calls(self, run_id: str) -> list[ToolCallResult]:
        self.actions.append(("tool_calls", run_id))
        return []


def _dispatcher() -> tuple[DispatchingAdapter, _SpyAdapter, _SpyAdapter]:
    raw = _SpyAdapter()
    graph = _SpyAdapter()
    dispatcher = DispatchingAdapter(
        {
            RuntimeAdapter.RAW_WORKER: raw,
            RuntimeAdapter.LANGGRAPH: graph,
        }
    )
    return dispatcher, raw, graph


def _context(
    make_manifest: Callable[..., AgentWorkload], *, run_id: str, adapter: str
) -> RunContext:
    workload = make_manifest(name=f"agent-{run_id}", adapter=adapter)
    now = datetime(2026, 1, 1, tzinfo=UTC)
    run = Run(
        id=run_id,
        workload_id=workload.name,
        caller="benchmark",
        state=RunState.QUEUED,
        created_at=now,
        updated_at=now,
        manifest_version=1,
    )
    return RunContext(run=run, workload=workload)


def test_register_and_submit_route_by_workload_adapter(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    dispatcher, raw, graph = _dispatcher()
    raw_workload = make_manifest(name="repo-agent", adapter="raw-worker")
    graph_workload = make_manifest(name="docs-agent", adapter="langgraph")

    dispatcher.register(raw_workload)
    dispatcher.register(graph_workload)
    dispatcher.submit(_context(make_manifest, run_id="run-1", adapter="raw-worker"))
    dispatcher.submit(_context(make_manifest, run_id="run-2", adapter="langgraph"))

    assert raw.registered == ["repo-agent"]
    assert graph.registered == ["docs-agent"]
    assert raw.submitted == ["run-1"]
    assert graph.submitted == ["run-2"]


def test_per_run_operations_route_to_the_recorded_adapter(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    dispatcher, raw, graph = _dispatcher()
    dispatcher.submit(_context(make_manifest, run_id="run-1", adapter="raw-worker"))
    dispatcher.submit(_context(make_manifest, run_id="run-2", adapter="langgraph"))

    dispatcher.pause("run-1")
    dispatcher.resume("run-2")
    dispatcher.cancel("run-1")
    dispatcher.status("run-2")
    dispatcher.usage("run-1")
    dispatcher.tool_calls("run-2")

    assert raw.actions == [("pause", "run-1"), ("cancel", "run-1"), ("usage", "run-1")]
    assert graph.actions == [
        ("resume", "run-2"),
        ("status", "run-2"),
        ("tool_calls", "run-2"),
    ]


def test_unconfigured_adapter_is_rejected(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    workload = make_manifest(name="docs-agent", adapter="langgraph")
    raw_only = DispatchingAdapter({RuntimeAdapter.RAW_WORKER: _SpyAdapter()})

    with pytest.raises(UnsupportedAdapterError):
        raw_only.register(workload)

    configured, _, graph = _dispatcher()
    configured.register(workload)
    assert graph.registered == [workload.name]


def test_unknown_run_is_rejected(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    dispatcher, _, _ = _dispatcher()

    with pytest.raises(UnsupportedAdapterError):
        dispatcher.status("never-submitted")
