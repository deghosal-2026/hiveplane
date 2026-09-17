"""An in-memory adapter that satisfies the contract (M16).

Used as the conformance baseline (M17) and as a stand-in until the raw-worker
and LangGraph adapters land (#43, #44). It records lifecycle state and tool
calls, reports no usage, and never decides policy.
"""

from __future__ import annotations

from hiveplane.core.run import RunState
from hiveplane.core.usage import UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.models import RunContext
from hiveplane.execution.tools import ToolCallResult


class StubAdapter:
    """An in-memory reference adapter implementing the ``Adapter`` contract."""

    def __init__(self) -> None:
        self._workloads: dict[str, AgentWorkload] = {}
        self._states: dict[str, RunState] = {}
        self._tool_calls: dict[str, list[ToolCallResult]] = {}

    def register(self, workload: AgentWorkload) -> None:
        """Record the workload the adapter is bound to."""
        self._workloads[workload.name] = workload

    def submit(self, context: RunContext) -> None:
        """Mark the run as running and start its tool-call log."""
        self._states[context.run.id] = RunState.RUNNING
        self._tool_calls.setdefault(context.run.id, [])

    def pause(self, run_id: str) -> bool:
        """Accept a pause request."""
        self._states[run_id] = RunState.PAUSED
        return True

    def resume(self, run_id: str) -> bool:
        """Accept a resume request."""
        self._states[run_id] = RunState.RUNNING
        return True

    def cancel(self, run_id: str) -> None:
        """Record the run as cancelled."""
        self._states[run_id] = RunState.CANCELLED

    def status(self, run_id: str) -> RunState:
        """Return the last recorded state, defaulting to queued."""
        return self._states.get(run_id, RunState.QUEUED)

    def usage(self, run_id: str) -> UsageReport | None:
        """Report no usage."""
        return None

    def tool_calls(self, run_id: str) -> list[ToolCallResult]:
        """Return the tool calls routed through the boundary for a run."""
        return list(self._tool_calls.get(run_id, []))
