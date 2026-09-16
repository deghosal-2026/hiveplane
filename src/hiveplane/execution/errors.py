"""Execution domain errors."""

from __future__ import annotations

from hiveplane.core.run import RunState
from hiveplane.execution.models import AdmissionResult


class ExecutionError(Exception):
    """Base class for execution errors."""


class RunNotFoundError(ExecutionError):
    """Raised when a run id is unknown to the store."""

    def __init__(self, run_id: str) -> None:
        super().__init__(f"run {run_id!r} not found")
        self.run_id = run_id


class IllegalTransitionError(ExecutionError):
    """Raised when a run state transition is not permitted."""

    def __init__(self, run_id: str, current: RunState, target: RunState) -> None:
        super().__init__(
            f"run {run_id!r} cannot transition from {current.value!r} to {target.value!r}"
        )
        self.run_id = run_id
        self.current = current
        self.target = target


class RunAdmissionRefusedError(ExecutionError):
    """Raised when a run is refused admission to its target context."""

    def __init__(self, result: AdmissionResult) -> None:
        reason = result.refused_reason or "admission refused"
        super().__init__(
            f"run for workload {result.workload!r} refused admission to "
            f"{result.context.value}: {reason}"
        )
        self.result = result


class RunNotIntervenableError(ExecutionError):
    """Raised when an intervention is invalid for the run's current state."""

    def __init__(self, run_id: str, action: str) -> None:
        super().__init__(f"run {run_id!r} cannot be {action} in its current state")
        self.run_id = run_id
        self.action = action
