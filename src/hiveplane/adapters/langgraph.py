"""The LangGraph adapter: wraps a compiled graph behind the Adapter contract (M17).

The graph runs on the configured spawner. Supersteps are streamed so the worker
can checkpoint cooperatively (operator pause/resume/cancel), and a LangGraph
``__interrupt__`` maps to a paused run that resumes with ``Command(resume=...)``.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any, cast

from hiveplane import telemetry
from hiveplane.adapters.errors import (
    EntrypointLoadError,
    MissingAdapterDependencyError,
    RunCancelledError,
    ToolCallEscalatedError,
    UnsupportedAdapterError,
    WorkerError,
)
from hiveplane.adapters.graph import CompiledGraph
from hiveplane.adapters.loader import EntrypointLoader
from hiveplane.adapters.reporter import RunReporter
from hiveplane.adapters.worker import RunControl, WorkerContext
from hiveplane.budget.pricing import CostTable
from hiveplane.core.event import EventType
from hiveplane.core.run import RunState
from hiveplane.core.spec import RuntimeAdapter
from hiveplane.core.usage import UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.errors import IllegalTransitionError
from hiveplane.execution.models import RunContext
from hiveplane.execution.tools import ToolCallResult, ToolGateway
from hiveplane.llm.provider import LLMProvider

_INTERRUPT_KEY = "__interrupt__"

#: Runs a unit of work; the default spawns a daemon thread.
Spawner = Callable[[Callable[[], None]], None]


def _require_command() -> Callable[..., Any]:
    """Return LangGraph's ``Command``, or raise if the extra is not installed."""
    try:
        from langgraph.types import Command
    except ImportError as exc:
        raise MissingAdapterDependencyError("langgraph", str(exc)) from exc
    return Command


def _thread_spawner(work: Callable[[], None]) -> None:
    threading.Thread(target=work, daemon=True).start()


def _configurable(run_id: str, ctx: WorkerContext) -> dict[str, Any]:
    return {"configurable": {"thread_id": run_id, "hiveplane_ctx": ctx}}


class LangGraphAdapter:
    """Runs a compiled LangGraph and reports through the control-plane boundary."""

    def __init__(
        self,
        reporter: RunReporter,
        tools: ToolGateway,
        loader: EntrypointLoader,
        *,
        clock: Callable[[], datetime] | None = None,
        spawner: Spawner | None = None,
        command_factory: Callable[..., Any] | None = None,
        provider: LLMProvider | None = None,
        cost_table: CostTable | None = None,
    ) -> None:
        self._reporter = reporter
        self._tools = tools
        self._loader = loader
        self._clock = clock or (lambda: datetime.now(UTC))
        self._spawner = spawner or _thread_spawner
        self._command_factory: Callable[..., Any] = command_factory or _require_command
        self._provider = provider
        self._cost_table = cost_table
        self._lock = threading.Lock()
        self._graphs: dict[str, CompiledGraph] = {}
        self._sessions: dict[str, tuple[RunContext, WorkerContext, RunControl]] = {}
        self._states: dict[str, RunState] = {}
        self._usage: dict[str, UsageReport | None] = {}
        self._interrupted: dict[str, bool] = {}
        self._escalated: dict[str, bool] = {}

    def register(self, workload: AgentWorkload) -> None:
        """Load and cache a workload's compiled graph, rejecting other adapters."""
        if workload.spec.runtime.adapter is not RuntimeAdapter.LANGGRAPH:
            raise UnsupportedAdapterError(workload.spec.runtime.adapter.value)
        graph = self._loader.load_object(workload.spec.runtime.entrypoint)
        for attr in ("stream", "get_state"):
            if not callable(getattr(graph, attr, None)):
                raise EntrypointLoadError(
                    workload.spec.runtime.entrypoint, f"graph has no {attr}()"
                )
        self._graphs[workload.name] = cast("CompiledGraph", graph)

    def submit(self, context: RunContext) -> None:
        """Start the graph on the configured spawner and return immediately."""
        graph = self._graph(context.workload)
        control = RunControl()
        ctx = WorkerContext(
            run=context.run,
            workload=context.workload,
            sandbox=context.sandbox,
            tools=self._tools,
            reporter=self._reporter,
            control=control,
            tool_calls=[],
            clock=self._clock,
            provider=self._provider,
            cost_table=self._cost_table,
        )
        with self._lock:
            self._states[context.run.id] = RunState.RUNNING
            self._sessions[context.run.id] = (context, ctx, control)
            self._usage[context.run.id] = None
            self._interrupted[context.run.id] = False
            self._escalated[context.run.id] = False
        self._spawner(
            telemetry.propagate_context(
                lambda: self._drive(graph, context, ctx, dict(context.run.task))
            )
        )

    def pause(self, run_id: str) -> bool:
        """Request a cooperative pause between supersteps."""
        session = self._sessions.get(run_id)
        if session is None:
            return False
        session[2].pause()
        return True

    def resume(self, run_id: str) -> bool:
        """Resume a paused run: review interrupt, cooperative pause, or escalation."""
        session = self._sessions.get(run_id)
        if session is None:
            return False
        context, ctx, control = session
        if self._escalated.get(run_id):
            # Re-dispatch (#129): the escalated tool call carries an approved
            # approval now, so drive the graph again from its initial task.
            graph = self._graph(context.workload)
            with self._lock:
                self._escalated[run_id] = False
                self._states[run_id] = RunState.RUNNING
            control.resume()  # clear the pause the escalation set
            self._spawner(
                telemetry.propagate_context(
                    lambda: self._drive(graph, context, ctx, dict(context.run.task))
                )
            )
            return True
        if self._interrupted.get(run_id):
            command = self._command_factory(resume=True)
            graph = self._graph(context.workload)
            with self._lock:
                self._interrupted[run_id] = False
                self._states[run_id] = RunState.RUNNING
            self._spawner(
                telemetry.propagate_context(lambda: self._drive(graph, context, ctx, command))
            )
        else:
            control.resume()
        return True

    def cancel(self, run_id: str) -> None:
        """Request cancellation; the run state is owned by the control plane."""
        session = self._sessions.get(run_id)
        if session is not None:
            session[2].cancel()

    def status(self, run_id: str) -> RunState:
        """Return the adapter's last recorded state."""
        return self._states.get(run_id, RunState.QUEUED)

    def usage(self, run_id: str) -> UsageReport | None:
        """Return the last usage report seen for the run."""
        return self._usage.get(run_id)

    def tool_calls(self, run_id: str) -> list[ToolCallResult]:
        """Return the tool calls the graph routed through the boundary."""
        session = self._sessions.get(run_id)
        return list(session[1].tool_calls) if session is not None else []

    def _graph(self, workload: AgentWorkload) -> CompiledGraph:
        graph = self._graphs.get(workload.name)
        if graph is None:
            self.register(workload)
            graph = self._graphs[workload.name]
        return graph

    def _drive(
        self,
        graph: CompiledGraph,
        context: RunContext,
        ctx: WorkerContext,
        payload: Any,
    ) -> None:
        run = context.run
        config = _configurable(run.id, ctx)
        with telemetry.span("execution", run=run, workload=context.workload) as active:
            try:
                for chunk in graph.stream(payload, config, stream_mode="values"):
                    if _INTERRUPT_KEY in chunk:
                        active.set_attribute("outcome", "paused")
                        self._paused(run.id)
                        return
                    ctx.checkpoint()
            except RunCancelledError:
                active.set_attribute("outcome", "cancelled")
                return
            except ToolCallEscalatedError:
                active.set_attribute("outcome", "escalated")
                # The gateway already paused the run at the service level; only
                # record internal state so resume() re-drives (#129).
                with self._lock:
                    self._escalated[run.id] = True
                    self._states[run.id] = RunState.PAUSED
                self._reporter.record_event(
                    run.id, EventType.OPERATOR_ACTION, "adapter", detail="tool call escalated"
                )
                return
            except WorkerError as exc:
                active.set_attribute("outcome", "failed")
                self._fail(run.id, str(exc))
                return
            except Exception as exc:
                active.set_attribute("outcome", "failed")
                self._fail(run.id, f"{type(exc).__name__}: {exc}")
                return
            snapshot = graph.get_state(config)
            if snapshot.next:
                active.set_attribute("outcome", "paused")
                self._paused(run.id)
                return
            active.set_attribute("outcome", "completed")
            with self._lock:
                self._states[run.id] = RunState.COMPLETED
            self._reporter.transition(
                run.id, RunState.COMPLETED, actor="adapter", result=snapshot.values
            )

    def _paused(self, run_id: str) -> None:
        with self._lock:
            self._interrupted[run_id] = True
            self._states[run_id] = RunState.PAUSED
        self._reporter.transition(run_id, RunState.PAUSED, actor="adapter", detail="interrupted")

    def _fail(self, run_id: str, reason: str) -> None:
        with suppress(IllegalTransitionError):
            self._reporter.transition(
                run_id,
                RunState.FAILED,
                actor="adapter",
                detail=reason,
                failure_reason=reason,
            )
        with self._lock:
            self._states[run_id] = RunState.FAILED
