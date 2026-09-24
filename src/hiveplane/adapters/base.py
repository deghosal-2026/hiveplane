"""The runtime adapter contract (M16, DD-02, DD-14).

Adapters translate the control-plane contract to a concrete runtime. They report
state transitions, tool calls, and usage; they never decide policy. Every tool
call routes through the control-plane tool boundary, and every model call is
checked against the certification attestation during admission.

:class:`Adapter` is the contract the raw-worker and LangGraph adapters implement
(#43, #44). :class:`AdapterRunExecutor` bridges an adapter onto the run
lifecycle's ``RunExecutor`` seam so the lifecycle never sees adapter internals.
"""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from hiveplane.core.run import RunState
from hiveplane.core.usage import UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.models import RunContext
from hiveplane.execution.tools import ToolCallResult


@runtime_checkable
class Adapter(Protocol):
    """The typed contract every runtime adapter implements."""

    def register(self, workload: AgentWorkload) -> None:
        """Bind the adapter to a workload manifest before any run starts."""
        ...

    def submit(self, context: RunContext) -> None:
        """Start a run; the adapter reports transitions and usage from here."""
        ...

    def pause(self, run_id: str) -> bool:
        """Cooperate with a pause request, returning whether it was accepted."""
        ...

    def resume(self, run_id: str) -> bool:
        """Resume a paused run, returning whether it was accepted."""
        ...

    def cancel(self, run_id: str) -> None:
        """Stop a run and release its resources."""
        ...

    def status(self, run_id: str) -> RunState:
        """Return the adapter's honest view of the run state."""
        ...

    def usage(self, run_id: str) -> UsageReport | None:
        """Return usage since the last report, if any."""
        ...

    def tool_calls(self, run_id: str) -> list[ToolCallResult]:
        """Return the tool calls the adapter routed through the boundary."""
        ...


class AdapterRunExecutor:
    """Adapts an :class:`Adapter` to the run lifecycle's ``RunExecutor`` seam."""

    def __init__(self, adapter: Adapter) -> None:
        self._adapter = adapter

    def start(self, context: RunContext) -> None:
        """Delegate run start to the adapter's ``submit``."""
        self._adapter.submit(context)

    def reattach(self, context: RunContext) -> bool:
        """Delegate a post-restart re-attach to the adapter, if it supports one."""
        reattach = getattr(self._adapter, "reattach", None)
        if reattach is None:
            return False
        return bool(reattach(context))

    def pause(self, run_id: str) -> bool:
        """Delegate a pause request to the adapter."""
        return self._adapter.pause(run_id)

    def resume(self, run_id: str) -> bool:
        """Delegate a resume request to the adapter."""
        return self._adapter.resume(run_id)

    def cancel(self, run_id: str) -> None:
        """Delegate a cancel request to the adapter."""
        self._adapter.cancel(run_id)

    def status(self, run_id: str) -> RunState:
        """Return the adapter's reported run state."""
        return self._adapter.status(run_id)

    def usage(self, run_id: str) -> UsageReport | None:
        """Return the adapter's pending usage report, if any."""
        return self._adapter.usage(run_id)
