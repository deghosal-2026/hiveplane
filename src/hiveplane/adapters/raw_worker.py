"""The raw Python worker adapter: the framework-free reference runtime (M16)."""

from __future__ import annotations

import threading
from collections.abc import Callable, Iterator
from contextlib import suppress
from datetime import UTC, datetime
from pathlib import Path
from typing import TYPE_CHECKING

from hiveplane import telemetry
from hiveplane.adapters.base import (
    AdapterCapabilities,
    AdapterEvent,
    buffered_stream,
)
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
from hiveplane.execution.sandbox_spec import SandboxWorkerSpec, worker_command
from hiveplane.execution.tools import ToolCallResult, ToolGateway
from hiveplane.llm.provider import LLMProvider

if TYPE_CHECKING:
    from hiveplane.api.sandbox_channel import SandboxChannel
    from hiveplane.execution.subprocess_spawner import SpawnOutcome, SubprocessSpawner

#: Terminal run states; a subprocess run is reconciled only if still live.
_TERMINAL = (RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED)

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
        root: str | Path | None = None,
        sandbox_channel: SandboxChannel | None = None,
        base_url: str | None = None,
        subprocess_spawner: SubprocessSpawner | None = None,
        sandbox_mode: str = "in-process",
    ) -> None:
        self._reporter = reporter
        self._tools = tools
        self._loader = loader
        self._clock = clock or (lambda: datetime.now(UTC))
        self._spawner = spawner or _thread_spawner
        self._provider = provider
        self._cost_table = cost_table
        self._root = str(root) if root is not None else "."
        self._sandbox_channel = sandbox_channel
        self._base_url = base_url
        self._subprocess_spawner = subprocess_spawner
        self._sandbox_mode = sandbox_mode
        self._lock = threading.Lock()
        self._entries: dict[str, Entrypoint] = {}
        self._states: dict[str, RunState] = {}
        self._controls: dict[str, RunControl] = {}
        self._contexts: dict[str, RunContext] = {}
        self._escalated: dict[str, bool] = {}
        self._tool_calls: dict[str, list[ToolCallResult]] = {}
        self._usage: dict[str, UsageReport | None] = {}
        self._identities: dict[str, str] = {}
        self._recovered: set[str] = set()

    def _subprocess_enabled(self, context: RunContext) -> bool:
        """Whether this run executes in the capped subprocess (#110)."""
        return (
            self._sandbox_mode == "subprocess"
            and context.sandbox
            and self._sandbox_channel is not None
            and self._base_url is not None
            and self._subprocess_spawner is not None
        )

    def _subprocess_execute(self, context: RunContext) -> None:
        """Run the entrypoint in a capped child that routes through the channel."""
        run = context.run
        assert self._sandbox_channel is not None
        assert self._base_url is not None
        assert self._subprocess_spawner is not None
        token = self._sandbox_channel.mint(run.id)
        sandbox = context.workload.spec.sandbox
        caps = sandbox.resource_caps if sandbox is not None else None
        spec = SandboxWorkerSpec(
            run_id=run.id,
            entrypoint=context.workload.spec.runtime.entrypoint,
            task=dict(run.task),
            base_url=self._base_url,
            token=token,
            root=self._root,
            resource_caps=caps,
        )
        command = worker_command(spec)
        spawner = self._subprocess_spawner

        def _launch() -> None:
            outcome = spawner.launch(command, caps)
            self._reconcile(run.id, outcome)

        threading.Thread(
            target=telemetry.propagate_context(_launch), daemon=True
        ).start()

    def _reconcile(self, run_id: str, outcome: SpawnOutcome) -> None:
        """Fail a run whose child died or exited without reporting a terminal state."""
        run = self._reporter.get(run_id)
        if run.state not in _TERMINAL:
            reason = outcome.failure_reason or "subprocess exited without reporting"
            with suppress(IllegalTransitionError):
                self._reporter.transition(
                    run_id,
                    RunState.FAILED,
                    actor="sandbox",
                    detail=reason,
                    failure_reason=reason,
                )
            with self._lock:
                self._states[run_id] = RunState.FAILED

    def register(self, workload: AgentWorkload) -> None:
        """Load and cache a workload's entrypoint, rejecting other adapters."""
        if workload.spec.runtime.adapter is not RuntimeAdapter.RAW_WORKER:
            raise UnsupportedAdapterError(workload.spec.runtime.adapter.value)
        self._entries[workload.name] = self._loader.load(workload.spec.runtime.entrypoint)

    def submit(self, context: RunContext) -> None:
        """Start the workload (in-process or in the capped subprocess)."""
        entry = self._entry(context.workload)
        control = RunControl()
        with self._lock:
            self._states[context.run.id] = RunState.RUNNING
            self._controls[context.run.id] = control
            self._contexts[context.run.id] = context
            self._escalated[context.run.id] = False
            self._tool_calls[context.run.id] = []
            self._usage[context.run.id] = None
        if self._subprocess_enabled(context):
            self._subprocess_execute(context)
            return
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

    def reattach(self, context: RunContext) -> bool:
        """Rebuild bookkeeping for a paused run recovered after a restart (#111).

        A raw worker cannot continue mid-flight, so a recovered run is marked to
        be re-driven from the start on resume (at-least-once, matching the
        escalation re-dispatch path).
        """
        run = context.run
        self._entry(context.workload)
        control = RunControl()
        with self._lock:
            self._states[run.id] = RunState.PAUSED
            self._controls[run.id] = control
            self._contexts[run.id] = context
            self._escalated[run.id] = False
            self._tool_calls.setdefault(run.id, [])
            self._usage.setdefault(run.id, None)
            self._recovered.add(run.id)
        return True

    def resume(self, run_id: str) -> bool:
        """Clear a pause request, or re-drive a run that escalated or was recovered.

        A raw worker cannot resume mid-flight, so a run that died on a tool
        escalation or a control-plane restart is executed again; the escalated
        call now carries an approved approval record and executes against the
        configured executor.
        """
        if self._escalated.get(run_id) or run_id in self._recovered:
            context = self._contexts.get(run_id)
            if context is None:
                return False
            with self._lock:
                self._escalated[run_id] = False
                self._recovered.discard(run_id)
                self._states[run_id] = RunState.RUNNING
            entry = self._entry(context.workload)
            run_control = self._controls[run_id]
            run_control.resume()  # clear the pause the escalation/recovery set
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

    def cancel(self, run_id: str, *, deadline_s: float | None = None) -> None:
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

    def capabilities(self) -> AdapterCapabilities:
        """Declare the raw worker's capabilities."""
        return AdapterCapabilities(
            streaming=False,
            pause_resume=True,
            state_edit=False,
            tool_execution=True,
            sandbox=True,
            deterministic_replay=True,
        )

    def stream(self, run_id: str) -> Iterator[AdapterEvent]:
        """Yield a buffered state stream for the run."""
        return buffered_stream(
            run_id, self.status(run_id), model_identity=self.model_identity(run_id)
        )

    def model_identity(self, run_id: str) -> str | None:
        """Return the model identity captured from inference, if any."""
        return self._identities.get(run_id)

    def conformance_version(self) -> str:
        """Conform to contract v2."""
        return "2"

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
            if ctx.reported_model_identity is not None:
                with self._lock:
                    self._identities[run.id] = ctx.reported_model_identity
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
