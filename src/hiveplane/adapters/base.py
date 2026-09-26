"""The runtime adapter contract (M16, DD-02, DD-14; v2 M31, DD-25).

Adapters translate the control-plane contract to a concrete runtime. They report
state transitions, tool calls, and usage; they never decide policy. Every tool
call routes through the control-plane tool boundary, and every model call is
checked against the certification attestation during admission.

:class:`Adapter` is the contract the raw-worker, LangGraph, PydanticAI, and
OpenAI Agents adapters implement. :class:`AdapterRunExecutor` bridges an adapter
onto the run lifecycle's ``RunExecutor`` seam so the lifecycle never sees adapter
internals.

Contract v2 (M31) makes the boundary explicit and versioned: adapters advertise
:class:`AdapterCapabilities`, expose an ordered :class:`AdapterEvent` stream,
report the model identity captured from actual inference, and declare their
``conformance_version()``. Adapters that predate v2 are tolerated through the
``*_of`` helpers, which fall back to v1 defaults.
"""

from __future__ import annotations

from collections.abc import Iterator
from enum import StrEnum
from typing import Protocol, runtime_checkable

from pydantic import BaseModel, ConfigDict, Field

from hiveplane.core.run import RunState
from hiveplane.core.usage import UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.models import RunContext
from hiveplane.execution.tools import ToolCallResult

#: The adapter contract version this module defines.
CONTRACT_VERSION = "2"

_TERMINAL = (RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED)


class AdapterEventKind(StrEnum):
    """The kinds of event an adapter may emit on its run stream."""

    STATE = "state"
    MODEL_DELTA = "model_delta"
    TOOL_CALL = "tool_call"
    TOOL_RESULT = "tool_result"
    CHECKPOINT = "checkpoint"
    USAGE = "usage"
    COMPLETED = "completed"


class AdapterEvent(BaseModel):
    """One ordered event on an adapter's run stream (contract v2)."""

    model_config = ConfigDict(extra="forbid")

    kind: AdapterEventKind
    run_id: str = Field(min_length=1)
    sequence: int = Field(default=0, ge=0)
    state: RunState | None = None
    text: str | None = None
    tool_id: str | None = None
    model_identity: str | None = None
    cost_usd: float | None = Field(default=None, ge=0.0)


class AdapterCapabilities(BaseModel):
    """What an adapter can do, negotiated at registration (contract v2)."""

    model_config = ConfigDict(extra="forbid")

    contract_version: str = CONTRACT_VERSION
    streaming: bool = False
    pause_resume: bool = True
    state_edit: bool = False
    tool_execution: bool = True
    sandbox: bool = True
    deterministic_replay: bool = False
    max_agent_depth: int = Field(default=0, ge=0)


@runtime_checkable
class Adapter(Protocol):
    """The typed contract every runtime adapter implements (v2)."""

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

    def cancel(self, run_id: str, *, deadline_s: float | None = None) -> None:
        """Stop a run and release its resources, within an optional deadline."""
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

    def capabilities(self) -> AdapterCapabilities:
        """Return the adapter's declared capabilities (contract v2)."""
        ...

    def stream(self, run_id: str) -> Iterator[AdapterEvent]:
        """Yield ordered events for a run; buffering adapters are tolerated."""
        ...

    def model_identity(self, run_id: str) -> str | None:
        """Return the model identity captured from actual inference, if any."""
        ...

    def conformance_version(self) -> str:
        """Return the contract version this adapter conforms to."""
        ...


def buffered_stream(
    run_id: str,
    state: RunState,
    *,
    model_identity: str | None = None,
    cost_usd: float | None = None,
    text: str | None = None,
) -> Iterator[AdapterEvent]:
    """Yield a state event, plus a completion event when the run is terminal.

    Adapters that do not stream incrementally use this to satisfy ``stream()``
    without inventing progress events: consumers tolerate buffering.
    """
    yield AdapterEvent(
        kind=AdapterEventKind.STATE,
        run_id=run_id,
        sequence=0,
        state=state,
        model_identity=model_identity,
        cost_usd=cost_usd,
    )
    if state in _TERMINAL:
        yield AdapterEvent(
            kind=AdapterEventKind.COMPLETED,
            run_id=run_id,
            sequence=1,
            state=state,
            text=text,
            model_identity=model_identity,
            cost_usd=cost_usd,
        )


def capabilities_of(adapter: object) -> AdapterCapabilities:
    """Return an adapter's capabilities, defaulting for pre-v2 adapters."""
    capabilities = getattr(adapter, "capabilities", None)
    if callable(capabilities):
        result = capabilities()
        if isinstance(result, AdapterCapabilities):
            return result
    return AdapterCapabilities(contract_version="1")


def conformance_version_of(adapter: object) -> str:
    """Return an adapter's conformance version, defaulting to v1."""
    version = getattr(adapter, "conformance_version", None)
    if callable(version):
        return str(version())
    return "1"


def model_identity_of(adapter: object, run_id: str) -> str | None:
    """Return the adapter-reported model identity, if it supports v2."""
    identity = getattr(adapter, "model_identity", None)
    if callable(identity):
        value = identity(run_id)
        return str(value) if value is not None else None
    return None


def stream_of(adapter: object, run_id: str) -> Iterator[AdapterEvent]:
    """Return an adapter's event stream, falling back to a buffered one."""
    stream = getattr(adapter, "stream", None)
    if callable(stream):
        return iter(stream(run_id))
    status = getattr(adapter, "status", None)
    if callable(status):
        return buffered_stream(run_id, status(run_id))
    return buffered_stream(run_id, RunState.QUEUED)


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

    def capabilities(self) -> AdapterCapabilities:
        """Return the wrapped adapter's capabilities."""
        return capabilities_of(self._adapter)

    def conformance_version(self) -> str:
        """Return the wrapped adapter's conformance version."""
        return conformance_version_of(self._adapter)

    def stream(self, run_id: str) -> Iterator[AdapterEvent]:
        """Return the wrapped adapter's event stream."""
        return stream_of(self._adapter, run_id)

    def model_identity(self, run_id: str) -> str | None:
        """Return the wrapped adapter's inferred model identity."""
        return model_identity_of(self._adapter, run_id)

