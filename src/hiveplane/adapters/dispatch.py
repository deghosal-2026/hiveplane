"""Per-workload adapter dispatch (M23, #109).

A single control plane may host workloads that use different runtime adapters
(e.g. repo-agent on raw-worker, docs-agent on langgraph). :class:`DispatchingAdapter`
implements the :class:`~hiveplane.adapters.base.Adapter` contract by routing each
workload to the adapter selected by ``spec.runtime.adapter`` and remembering which
adapter owns each run so per-run operations reach the right runtime.
"""

from __future__ import annotations

import threading
from collections.abc import Iterator, Mapping

from hiveplane.adapters.base import (
    Adapter,
    AdapterCapabilities,
    AdapterEvent,
    capabilities_of,
    conformance_version_of,
    model_identity_of,
    stream_of,
)
from hiveplane.adapters.errors import UnsupportedAdapterError
from hiveplane.core.run import RunState
from hiveplane.core.spec import RuntimeAdapter
from hiveplane.core.usage import UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.models import RunContext
from hiveplane.execution.tools import ToolCallResult


class DispatchingAdapter:
    """Routes runs to the adapter named by each workload's manifest."""

    def __init__(self, adapters: Mapping[RuntimeAdapter, Adapter]) -> None:
        self._adapters: dict[RuntimeAdapter, Adapter] = dict(adapters)
        self._selected: dict[str, Adapter] = {}
        self._lock = threading.Lock()

    @property
    def adapters(self) -> Mapping[RuntimeAdapter, Adapter]:
        """Return the configured adapter per runtime selection."""
        return dict(self._adapters)

    def register(self, workload: AgentWorkload) -> None:
        """Register ``workload`` with the adapter its manifest selects."""
        self._adapter_for(workload).register(workload)

    def submit(self, context: RunContext) -> None:
        """Route a run to its workload's adapter and remember the owner."""
        adapter = self._adapter_for(context.workload)
        with self._lock:
            self._selected[context.run.id] = adapter
        adapter.submit(context)

    def reattach(self, context: RunContext) -> bool:
        """Route a recovered run to its workload's adapter and remember the owner."""
        adapter = self._adapter_for(context.workload)
        with self._lock:
            self._selected[context.run.id] = adapter
        reattach = getattr(adapter, "reattach", None)
        if reattach is None:
            return False
        return bool(reattach(context))

    def pause(self, run_id: str) -> bool:
        """Pause the run on the adapter that owns it."""
        return self._owner(run_id).pause(run_id)

    def resume(self, run_id: str) -> bool:
        """Resume the run on the adapter that owns it."""
        return self._owner(run_id).resume(run_id)

    def cancel(self, run_id: str, *, deadline_s: float | None = None) -> None:
        """Cancel the run on the adapter that owns it."""
        self._owner(run_id).cancel(run_id, deadline_s=deadline_s)

    def status(self, run_id: str) -> RunState:
        """Return the owning adapter's view of a run's state."""
        return self._owner(run_id).status(run_id)

    def usage(self, run_id: str) -> UsageReport | None:
        """Return usage from the adapter that owns the run."""
        return self._owner(run_id).usage(run_id)

    def tool_calls(self, run_id: str) -> list[ToolCallResult]:
        """Return tool calls from the adapter that owns the run."""
        return self._owner(run_id).tool_calls(run_id)

    def capabilities(self) -> AdapterCapabilities:
        """Return the union of the configured adapters' capabilities."""
        capabilities = [capabilities_of(adapter) for adapter in self._adapters.values()]
        if not capabilities:
            return AdapterCapabilities()
        return AdapterCapabilities(
            streaming=any(item.streaming for item in capabilities),
            pause_resume=all(item.pause_resume for item in capabilities),
            state_edit=any(item.state_edit for item in capabilities),
            tool_execution=all(item.tool_execution for item in capabilities),
            sandbox=all(item.sandbox for item in capabilities),
            deterministic_replay=all(item.deterministic_replay for item in capabilities),
            max_agent_depth=max(item.max_agent_depth for item in capabilities),
        )

    def stream(self, run_id: str) -> Iterator[AdapterEvent]:
        """Return the owning adapter's event stream."""
        return stream_of(self._owner(run_id), run_id)

    def model_identity(self, run_id: str) -> str | None:
        """Return the owning adapter's inferred model identity."""
        return model_identity_of(self._owner(run_id), run_id)

    def conformance_version(self) -> str:
        """Return the configured adapters' conformance version."""
        versions = {conformance_version_of(adapter) for adapter in self._adapters.values()}
        if len(versions) == 1:
            return versions.pop()
        return "2"

    def _adapter_for(self, workload: AgentWorkload) -> Adapter:
        selected = workload.spec.runtime.adapter
        adapter = self._adapters.get(selected)
        if adapter is None:
            raise UnsupportedAdapterError(selected.value)
        return adapter

    def _owner(self, run_id: str) -> Adapter:
        with self._lock:
            adapter = self._selected.get(run_id)
        if adapter is None:
            raise UnsupportedAdapterError(f"no submitted run {run_id!r}")
        return adapter
