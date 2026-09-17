"""Tests for adapter and worker errors."""

from __future__ import annotations

from hiveplane.adapters.errors import (
    AdapterError,
    EntrypointLoadError,
    RunCancelledError,
    RunTerminatedError,
    ToolCallDeniedError,
    UnsupportedAdapterError,
    WorkerError,
)
from hiveplane.core.run import RunState
from hiveplane.execution.tools import ToolCallOutcome, ToolCallResult


def _result(outcome: ToolCallOutcome = ToolCallOutcome.DENIED) -> ToolCallResult:
    return ToolCallResult(run_id="run-1", tool_id="mcp.t.x", outcome=outcome, reason="nope")


def test_errors_share_a_base() -> None:
    assert issubclass(WorkerError, AdapterError)
    assert issubclass(UnsupportedAdapterError, AdapterError)
    assert issubclass(EntrypointLoadError, AdapterError)


def test_unsupported_adapter_carries_name() -> None:
    exc = UnsupportedAdapterError("langgraph")
    assert exc.adapter == "langgraph"
    assert "langgraph" in str(exc)


def test_entrypoint_load_error_carries_context() -> None:
    exc = EntrypointLoadError("a.b:c", "no module named a.b")
    assert exc.entrypoint == "a.b:c"
    assert exc.reason == "no module named a.b"


def test_tool_call_denied_carries_result() -> None:
    exc = ToolCallDeniedError(_result())
    assert exc.result.tool_id == "mcp.t.x"


def test_run_terminated_carries_state() -> None:
    assert RunTerminatedError(RunState.FAILED).state is RunState.FAILED


def test_run_cancelled_carries_state() -> None:
    assert RunCancelledError(RunState.CANCELLED).state is RunState.CANCELLED


def test_error_is_an_exception() -> None:
    assert isinstance(RunTerminatedError(RunState.FAILED), Exception)


def test_missing_dependency_carries_adapter_and_reason() -> None:
    from hiveplane.adapters.errors import MissingAdapterDependencyError

    exc = MissingAdapterDependencyError("langgraph", "no module named langgraph")
    assert exc.adapter == "langgraph"
    assert exc.reason == "no module named langgraph"
    assert "langgraph" in str(exc)

