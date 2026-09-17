"""Adapter and worker domain errors (M16)."""

from __future__ import annotations

from hiveplane.core.run import RunState
from hiveplane.execution.tools import ToolCallResult


class AdapterError(Exception):
    """Base class for runtime adapter errors."""


class UnsupportedAdapterError(AdapterError):
    """Raised when an adapter is asked to run a workload it does not support."""

    def __init__(self, adapter: str) -> None:
        super().__init__(f"adapter {adapter!r} is not supported by this runtime")
        self.adapter = adapter


class EntrypointLoadError(AdapterError):
    """Raised when a workload entrypoint cannot be resolved or imported."""

    def __init__(self, entrypoint: str, reason: str) -> None:
        super().__init__(f"cannot load entrypoint {entrypoint!r}: {reason}")
        self.entrypoint = entrypoint
        self.reason = reason


class MissingAdapterDependencyError(AdapterError):
    """Raised when an adapter's optional runtime dependency is not installed."""

    def __init__(self, adapter: str, reason: str) -> None:
        super().__init__(f"adapter {adapter!r} is unavailable: {reason}")
        self.adapter = adapter
        self.reason = reason


class WorkerError(AdapterError):
    """Base class for errors a worker raises through the control plane."""


class ToolCallDeniedError(WorkerError):
    """Raised when the tool boundary denies a tool call."""

    def __init__(self, result: ToolCallResult) -> None:
        super().__init__(f"tool call {result.tool_id!r} denied: {result.reason}")
        self.result = result


class ToolCallBlockedError(WorkerError):
    """Raised when the tool boundary blocks a tool call for injection."""

    def __init__(self, result: ToolCallResult) -> None:
        super().__init__(f"tool call {result.tool_id!r} blocked: {result.reason}")
        self.result = result


class ToolCallEscalatedError(WorkerError):
    """Raised when a tool call requires approval and the run is paused."""

    def __init__(self, result: ToolCallResult) -> None:
        super().__init__(f"tool call {result.tool_id!r} requires approval")
        self.result = result


class RunTerminatedError(WorkerError):
    """Raised when the run reached a terminal state during a report."""

    def __init__(self, state: RunState) -> None:
        super().__init__(f"run is {state.value}")
        self.state = state


class RunCancelledError(WorkerError):
    """Raised at a checkpoint when the run has been cancelled."""

    def __init__(self, state: RunState = RunState.CANCELLED) -> None:
        super().__init__(f"run is {state.value}")
        self.state = state
