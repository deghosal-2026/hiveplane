"""The PydanticAI adapter: wraps a ``pydantic_ai.Agent`` behind the contract (M31).

Inference is governed: the adapter installs a custom PydanticAI model whose
``request`` routes through :meth:`WorkerContext.complete`, so every model call
passes the identity check, usage pricing, and budget boundary. Framework types
are converted at this boundary; nothing from PydanticAI crosses into core.
"""

from __future__ import annotations

import json
import os
import threading
from collections.abc import Callable, Iterator
from contextlib import suppress
from datetime import UTC, datetime
from typing import Any

from hiveplane import telemetry
from hiveplane.adapters.base import (
    AdapterCapabilities,
    AdapterEvent,
    buffered_stream,
)
from hiveplane.adapters.errors import (
    EntrypointLoadError,
    MissingAdapterDependencyError,
    RunCancelledError,
    ToolCallEscalatedError,
    UnsupportedAdapterError,
    WorkerError,
)
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

Spawner = Callable[[Callable[[], None]], None]

#: The governed model class, built lazily so importing this module never needs
#: the optional ``pydantic-ai`` extra.
_MODEL_CLASS: type | None = None


def _thread_spawner(work: Callable[[], None]) -> None:
    threading.Thread(target=work, daemon=True).start()


def _require_pydantic_ai() -> None:
    """Import PydanticAI or raise a dependency error naming the extra."""
    os.environ.setdefault("PYDANTIC_AI_NO_BANNER", "1")
    try:
        import pydantic_ai  # noqa: F401
    except ImportError as exc:
        raise MissingAdapterDependencyError("pydantic-ai", str(exc)) from exc


def _flatten(messages: list[Any]) -> str:
    """Flatten PydanticAI messages into a single governed prompt."""
    chunks: list[str] = []
    for message in messages:
        for part in getattr(message, "parts", []):
            content = getattr(part, "content", None)
            if isinstance(content, str):
                chunks.append(content)
    return "\n".join(chunks) or " "


def governed_model_class() -> type:
    """Return (building once) the PydanticAI model that uses the governed seam."""
    global _MODEL_CLASS
    if _MODEL_CLASS is None:
        _require_pydantic_ai()
        from pydantic_ai.messages import ModelResponse, TextPart
        from pydantic_ai.models import Model

        class HiveplaneModel(Model):
            """A PydanticAI model that routes inference through ``ctx.complete``."""

            def __init__(self, ctx: WorkerContext, identity: str) -> None:
                super().__init__()
                self._ctx = ctx
                self._identity = identity

            @property
            def model_name(self) -> str:
                return self._identity

            @property
            def system(self) -> str:
                return "hiveplane"

            async def request(
                self, messages: Any, model_settings: Any, model_request_parameters: Any
            ) -> Any:
                result = self._ctx.complete(_flatten(messages))
                return ModelResponse(parts=[TextPart(content=result.content)])

        _MODEL_CLASS = HiveplaneModel
    return _MODEL_CLASS


def _prompt_from_task(task: dict[str, Any]) -> str:
    """Extract the agent prompt from the task payload."""
    for key in ("prompt", "input", "message", "text"):
        value = task.get(key)
        if isinstance(value, str) and value:
            return value
    return json.dumps(task, sort_keys=True) if task else ""


def _is_agent(obj: object) -> bool:
    return callable(getattr(obj, "run_sync", None))


class PydanticAIAdapter:
    """Runs a PydanticAI agent with governed inference."""

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
        model_factory: Callable[[WorkerContext, str], Any] | None = None,
    ) -> None:
        self._reporter = reporter
        self._tools = tools
        self._loader = loader
        self._clock = clock or (lambda: datetime.now(UTC))
        self._spawner = spawner or _thread_spawner
        self._provider = provider
        self._cost_table = cost_table
        self._model_factory = model_factory
        self._lock = threading.Lock()
        self._agents: dict[str, Any] = {}
        self._sessions: dict[str, tuple[RunContext, WorkerContext, RunControl]] = {}
        self._states: dict[str, RunState] = {}

    def register(self, workload: AgentWorkload) -> None:
        """Load and cache a workload's agent, rejecting other adapters."""
        if workload.spec.runtime.adapter is not RuntimeAdapter.PYDANTIC_AI:
            raise UnsupportedAdapterError(workload.spec.runtime.adapter.value)
        _require_pydantic_ai()
        obj = self._loader.load_object(workload.spec.runtime.entrypoint)
        if not (_is_agent(obj) or callable(obj)):
            raise EntrypointLoadError(
                workload.spec.runtime.entrypoint,
                "entrypoint is not a PydanticAI Agent or agent factory",
            )
        self._agents[workload.name] = obj

    def submit(self, context: RunContext) -> None:
        """Start the agent on the configured spawner and return immediately."""
        obj = self._agent(context.workload)
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
        self._spawner(
            telemetry.propagate_context(lambda: self._run(obj, context, ctx))
        )

    def pause(self, run_id: str) -> bool:
        """PydanticAI's synchronous run cannot pause mid-flight; refuse."""
        return False

    def resume(self, run_id: str) -> bool:
        """Nothing to resume; refuse."""
        return False

    def cancel(self, run_id: str, *, deadline_s: float | None = None) -> None:
        """Request cancellation; the run state is owned by the control plane."""
        session = self._sessions.get(run_id)
        if session is not None:
            session[2].cancel()

    def status(self, run_id: str) -> RunState:
        """Return the adapter's last recorded state."""
        return self._states.get(run_id, RunState.QUEUED)

    def usage(self, run_id: str) -> UsageReport | None:
        """Return the last usage report seen for the run."""
        session = self._sessions.get(run_id)
        return None if session is None else session[1].last_usage

    def tool_calls(self, run_id: str) -> list[ToolCallResult]:
        """Return the tool calls routed through the boundary."""
        session = self._sessions.get(run_id)
        return list(session[1].tool_calls) if session is not None else []

    def capabilities(self) -> AdapterCapabilities:
        """Declare the PydanticAI adapter's capabilities."""
        return AdapterCapabilities(
            streaming=False,
            pause_resume=False,
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
        session = self._sessions.get(run_id)
        return None if session is None else session[1].reported_model_identity

    def conformance_version(self) -> str:
        """Conform to contract v2."""
        return "2"

    def _agent(self, workload: AgentWorkload) -> Any:
        obj = self._agents.get(workload.name)
        if obj is None:
            self.register(workload)
            obj = self._agents[workload.name]
        return obj

    def _build_model(self, ctx: WorkerContext, identity: str) -> Any:
        if self._model_factory is not None:
            return self._model_factory(ctx, identity)
        return governed_model_class()(ctx, identity)

    def _run(self, obj: Any, context: RunContext, ctx: WorkerContext) -> None:
        run = context.run
        with telemetry.span("execution", run=run, workload=context.workload) as active:
            try:
                identity = ctx.model_identity or ""
                model = self._build_model(ctx, identity)
                agent = obj(model) if callable(obj) and not _is_agent(obj) else obj
                if _is_agent(agent):
                    agent.model = model
                result = agent.run_sync(_prompt_from_task(dict(run.task)))
                output = getattr(result, "output", None)
            except RunCancelledError:
                active.set_attribute("outcome", "cancelled")
                return
            except ToolCallEscalatedError:
                active.set_attribute("outcome", "escalated")
                with self._lock:
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
            active.set_attribute("outcome", "completed")
            self._reporter.transition(run.id, RunState.COMPLETED, actor="adapter", result=output)
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
