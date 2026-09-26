"""The OpenAI Agents SDK adapter: wraps ``agents.Agent`` behind the contract (M31).

Inference is governed: the adapter installs a custom OpenAI Agents ``Model``
whose ``get_response`` routes through :meth:`WorkerContext.complete`, so every
model call passes the identity check, usage pricing, and budget boundary.
Framework types are converted at this boundary; nothing from the Agents SDK
crosses into core.
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
#: the optional ``openai-agents`` extra.
_MODEL_CLASS: type | None = None


def _thread_spawner(work: Callable[[], None]) -> None:
    threading.Thread(target=work, daemon=True).start()


def _require_openai_agents() -> None:
    """Import the Agents SDK or raise a dependency error naming the extra."""
    os.environ.setdefault("OPENAI_AGENTS_DISABLE_TRACING", "1")
    try:
        import agents  # noqa: F401
    except ImportError as exc:
        raise MissingAdapterDependencyError("openai-agents", str(exc)) from exc


def _flatten(input_value: Any) -> str:
    """Flatten the Agents SDK input into a single governed prompt."""
    if isinstance(input_value, str):
        return input_value
    chunks: list[str] = []
    for item in input_value or []:
        if isinstance(item, str):
            chunks.append(item)
            continue
        content = item.get("content") if isinstance(item, dict) else getattr(item, "content", None)
        if isinstance(content, str):
            chunks.append(content)
        elif isinstance(content, list):
            for part in content:
                text = part.get("text") if isinstance(part, dict) else getattr(part, "text", None)
                if isinstance(text, str):
                    chunks.append(text)
    return "\n".join(chunks) or " "


def governed_model_class() -> type:
    """Return (building once) the Agents SDK model that uses the governed seam."""
    global _MODEL_CLASS
    if _MODEL_CLASS is None:
        _require_openai_agents()
        from agents.items import (  # type: ignore[attr-defined]
            ResponseOutputMessage,
            ResponseOutputText,
        )
        from agents.models.interface import Model, ModelResponse  # type: ignore[attr-defined]
        from agents.usage import Usage

        class HiveplaneOpenAIModel(Model):
            """An Agents SDK model that routes inference through ``ctx.complete``."""

            def __init__(self, ctx: WorkerContext, identity: str) -> None:
                self._ctx = ctx
                self._identity = identity

            async def get_response(
                self,
                system_instructions: Any,
                input: Any,  # noqa: A002 (matches the Agents SDK signature)
                model_settings: Any,
                tools: Any,
                output_schema: Any,
                handoffs: Any,
                tracing: Any,
                **kwargs: Any,
            ) -> Any:
                result = self._ctx.complete(_flatten(input))
                text = ResponseOutputText(
                    text=result.content, type="output_text", annotations=[]
                )
                message = ResponseOutputMessage(
                    id="hiveplane",
                    type="message",
                    role="assistant",
                    status="completed",
                    content=[text],
                )
                return ModelResponse(output=[message], usage=Usage(), response_id=None)

            async def stream_response(  # type: ignore[override]
                self, *args: Any, **kwargs: Any
            ) -> Any:
                raise NotImplementedError("streaming is not supported by HiveplaneModel")

        _MODEL_CLASS = HiveplaneOpenAIModel
    return _MODEL_CLASS


def _prompt_from_task(task: dict[str, Any]) -> str:
    """Extract the agent prompt from the task payload."""
    for key in ("prompt", "input", "message", "text"):
        value = task.get(key)
        if isinstance(value, str) and value:
            return value
    return json.dumps(task, sort_keys=True) if task else ""


def _is_agent(obj: object) -> bool:
    return callable(getattr(obj, "get_system_prompt", None)) or callable(
        getattr(obj, "run", None)
    )


class OpenAIAgentsAdapter:
    """Runs an OpenAI Agents SDK agent with governed inference."""

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
        if workload.spec.runtime.adapter is not RuntimeAdapter.OPENAI_AGENTS:
            raise UnsupportedAdapterError(workload.spec.runtime.adapter.value)
        _require_openai_agents()
        obj = self._loader.load_object(workload.spec.runtime.entrypoint)
        if not (_is_agent(obj) or callable(obj)):
            raise EntrypointLoadError(
                workload.spec.runtime.entrypoint,
                "entrypoint is not an Agents SDK Agent or agent factory",
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
        """The Agents SDK run cannot pause mid-flight; refuse."""
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
        """Declare the OpenAI Agents adapter's capabilities."""
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
                from agents import Runner

                identity = ctx.model_identity or ""
                model = self._build_model(ctx, identity)
                agent = obj(model) if callable(obj) and not _is_agent(obj) else obj
                if _is_agent(agent):
                    agent.model = model
                result = Runner.run_sync(agent, input=_prompt_from_task(dict(run.task)))
                output = getattr(result, "final_output", None)
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
