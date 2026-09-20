"""The raw Python worker adapter: the framework-free reference runtime (M16)."""

from __future__ import annotations

import threading
from collections.abc import Callable
from contextlib import suppress
from datetime import UTC, datetime

from hiveplane import telemetry
from hiveplane.adapters.errors import (
    RunCancelledError,
    ToolCallEscalatedError,
    UnsupportedAdapterError,
    WorkerError,
)
from hiveplane.adapters.loader import Entrypoint, EntrypointLoader
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

#: Runs a unit of work; the default spawns a daemon thread.
Spawner = Callable[[Callable[[], None]], None]


def _thread_spawner(work: Callable[[], None]) -> None:
    threading.Thread(target=work, daemon=True).start()


class RawWorkerAdapter:
    """Runs a workload entrypoint in-process and reports through the boundary."""

    def __init__(
        self,
        reporter: RunReporter,
        tools: ToolGateway,
        loader: EntrypointLoader,
        *,
        clock: Callable[[], datetime] | None = None,
        spawner: Spawner | None = None,
        provider: LLMProvider | None = None,
        cost_table: CostTable | None = None,
    ) -> None:
        self._reporter = reporter
        self._tools = tools
        self._loader = loader
        self._clock = clock or (lambda: datetime.now(UTC))
        self._spawner = spawner or _thread_spawner
        self._provider = provider
        self._cost_table = cost_table
        self._lock = threading.Lock()
        self._entries: dict[str, Entrypoint] = {}
        self._states: dict[str, RunState] = {}
        self._controls: dict[str, RunControl] = {}
        self._contexts: dict[str, RunContext] = {}
        self._escalated: dict[str, bool] = {}
        self._tool_calls: dict[str, list[ToolCallResult]] = {}
        self._usage: dict[str, UsageReport | None] = {}

    def register(self, workload: AgentWorkload) -> None:
        """Load and cache a workload's entrypoint, rejecting other adapters."""
        if workload.spec.runtime.adapter is not RuntimeAdapter.RAW_WORKER:
            raise UnsupportedAdapterError(workload.spec.runtime.adapter.value)
        self._entries[workload.name] = self._loader.load(workload.spec.runtime.entrypoint)

    def submit(self, context: RunContext) -> None:
        """Start the workload on the configured spawner and return immediately."""
        entry = self._entry(context.workload)
        control = RunControl()
        with self._lock:
            self._states[context.run.id] = RunState.RUNNING
            self._controls[context.run.id] = control
            self._contexts[context.run.id] = context
            self._escalated[context.run.id] = False
            self._tool_calls[context.run.id] = []
            self._usage[context.run.id] = None
        self._spawner(
            telemetry.propagate_context(lambda: self._execute(entry, context, control))
        )

    def pause(self, run_id: str) -> bool:
        """Request a cooperative pause."""
        control = self._controls.get(run_id)
        if control is None:
            return False
        control.pause()
        return True

    def resume(self, run_id: str) -> bool:
        """Clear a pause request, or re-drive a run that escalated (#129).

        A raw worker cannot resume mid-flight, so a run that died on a tool
        escalation is executed again; the escalated call now carries an
        approved approval record and executes against the configured executor.
        """
        if self._escalated.get(run_id):
            context = self._contexts.get(run_id)
            if context is None:
                return False
            with self._lock:
                self._escalated[run_id] = False
                self._states[run_id] = RunState.RUNNING
            entry = self._entry(context.workload)
            run_control = self._controls[run_id]
            run_control.resume()  # clear the pause the escalation set
            self._spawner(
                telemetry.propagate_context(
                    lambda: self._execute(entry, context, run_control)
                )
            )
            return True
        control = self._controls.get(run_id)
        if control is None:
            return False
        control.resume()
        return True

    def cancel(self, run_id: str) -> None:
        """Request cancellation; the run state is owned by the control plane."""
        control = self._controls.get(run_id)
        if control is not None:
            control.cancel()

    def status(self, run_id: str) -> RunState:
        """Return the adapter's last recorded state."""
        return self._states.get(run_id, RunState.QUEUED)

    def usage(self, run_id: str) -> UsageReport | None:
        """Return the last usage report seen for the run."""
        return self._usage.get(run_id)

    def tool_calls(self, run_id: str) -> list[ToolCallResult]:
        """Return the tool calls routed through the boundary for the run."""
        return list(self._tool_calls.get(run_id, []))

    def _entry(self, workload: AgentWorkload) -> Entrypoint:
        entry = self._entries.get(workload.name)
        if entry is None:
            self.register(workload)
            entry = self._entries[workload.name]
        return entry

    def _execute(self, entry: Entrypoint, context: RunContext, control: RunControl) -> None:
        run = context.run
        tool_calls = self._tool_calls[run.id]
        ctx = WorkerContext(
            run=run,
            workload=context.workload,
            sandbox=context.sandbox,
            tools=self._tools,
            reporter=self._reporter,
            control=control,
            tool_calls=tool_calls,
            clock=self._clock,
            provider=self._provider,
            cost_table=self._cost_table,
        )
        with telemetry.span("execution", run=run, workload=context.workload) as active:
            try:
                result = entry(ctx.task, ctx)
            except RunCancelledError:
                active.set_attribute("outcome", "cancelled")
                return
            except ToolCallEscalatedError:
                active.set_attribute("outcome", "escalated")
                with self._lock:
                    self._escalated[run.id] = True
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
            active.set_attribute("outcome", "completed")
            self._reporter.transition(run.id, RunState.COMPLETED, actor="adapter", result=result)
            with self._lock:
                self._states[run.id] = RunState.COMPLETED

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
