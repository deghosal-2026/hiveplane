"""Tests for the runtime adapter contract and its execution-seam bridge (M16)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane.adapters.base import Adapter, AdapterRunExecutor
from hiveplane.adapters.stub import StubAdapter
from hiveplane.core.run import Run, RunState
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.models import RunContext


def _clock() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def _run(run_id: str = "run-1", workload: str = "agent-1") -> Run:
    return Run(
        id=run_id,
        workload_id=workload,
        caller="cli",
        state=RunState.QUEUED,
        created_at=_clock(),
        updated_at=_clock(),
    )


def test_stub_adapter_satisfies_contract() -> None:
    assert isinstance(StubAdapter(), Adapter)


def test_stub_adapter_tracks_full_lifecycle(make_manifest: Callable[..., AgentWorkload]) -> None:
    adapter = StubAdapter()
    workload = make_manifest()
    adapter.register(workload)

    adapter.submit(RunContext(run=_run(), workload=workload, sandbox=False))
    assert adapter.status("run-1") is RunState.RUNNING

    assert adapter.pause("run-1") is True
    assert adapter.status("run-1") is RunState.PAUSED

    assert adapter.resume("run-1") is True
    assert adapter.status("run-1") is RunState.RUNNING

    adapter.cancel("run-1")
    assert adapter.status("run-1") is RunState.CANCELLED
    assert adapter.usage("run-1") is None
    assert adapter.tool_calls("run-1") == []


def test_stub_adapter_defaults_unknown_runs_to_queued() -> None:
    assert StubAdapter().status("missing") is RunState.QUEUED


def test_adapter_run_executor_bridges_lifecycle(
    make_manifest: Callable[..., AgentWorkload],
) -> None:
    adapter = StubAdapter()
    executor = AdapterRunExecutor(adapter)
    workload = make_manifest()

    executor.start(RunContext(run=_run(), workload=workload, sandbox=True))
    assert adapter.status("run-1") is RunState.RUNNING
    assert executor.status("run-1") is RunState.RUNNING

    assert executor.pause("run-1") is True
    assert executor.status("run-1") is RunState.PAUSED

    assert executor.resume("run-1") is True
    executor.cancel("run-1")
    assert executor.status("run-1") is RunState.CANCELLED
    assert executor.usage("run-1") is None
