"""The worker-facing control-plane client (M16).

A workload entrypoint receives a :class:`WorkerContext` that routes tool calls
through the policy boundary, reports usage, and offers cooperative pause/cancel
checkpoints. Workers report; the control plane decides.
"""

from __future__ import annotations

import threading
from collections.abc import Callable
from datetime import datetime

from pydantic import JsonValue

from hiveplane import metrics, telemetry
from hiveplane.adapters.errors import (
    ModelIdentityMismatchError,
    RunCancelledError,
    RunTerminatedError,
    ToolCallBlockedError,
    ToolCallDeniedError,
    ToolCallEscalatedError,
)
from hiveplane.adapters.reporter import RunReporter
from hiveplane.core.decision import ActionClass, DataSensitivity
from hiveplane.core.event import EventType
from hiveplane.core.run import Run, RunState
from hiveplane.core.spec import canonical_model_identity, validate_model_identity
from hiveplane.core.usage import UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.execution.tools import (
    ToolCallOutcome,
    ToolCallRequest,
    ToolCallResult,
    ToolGateway,
)
from hiveplane.llm.models import (
    CompletionRequest,
    CompletionResult,
    Message,
    TokenUsage,
)
from hiveplane.llm.provider import LLMProvider

_TERMINAL = (RunState.COMPLETED, RunState.FAILED, RunState.CANCELLED)


def _default_provider() -> LLMProvider:
    """Resolve the configured provider when the adapter injected none."""
    from hiveplane.llm.factory import build_provider

    return build_provider()


class RunControl:
    """Cooperative pause/cancel signaling for a live run."""

    def __init__(self) -> None:
        self._condition = threading.Condition()
        self._paused = False
        self._cancelled = False

    @property
    def paused(self) -> bool:
        """Return True while the run is paused."""
        with self._condition:
            return self._paused

    @property
    def cancelled(self) -> bool:
        """Return True once the run has been cancelled."""
        with self._condition:
            return self._cancelled

    def pause(self) -> None:
        """Request a cooperative pause."""
        with self._condition:
            self._paused = True

    def resume(self) -> None:
        """Clear a pause request and wake any blocked checkpoint."""
        with self._condition:
            self._paused = False
            self._condition.notify_all()

    def cancel(self) -> None:
        """Request cancellation and wake any blocked checkpoint."""
        with self._condition:
            self._cancelled = True
            self._condition.notify_all()

    def checkpoint(self) -> None:
        """Block while paused, raising when the run has been cancelled."""
        with self._condition:
            while self._paused and not self._cancelled:
                self._condition.wait()
            if self._cancelled:
                raise RunCancelledError(RunState.CANCELLED)


class WorkerContext:
    """The control-plane client handed to a workload entrypoint."""

    def __init__(
        self,
        *,
        run: Run,
        workload: AgentWorkload,
        sandbox: bool,
        tools: ToolGateway,
        reporter: RunReporter,
        control: RunControl,
        tool_calls: list[ToolCallResult],
        clock: Callable[[], datetime],
        provider: LLMProvider | None = None,
    ) -> None:
        self._run = run
        self._workload = workload
        self._sandbox = sandbox
        self._tools = tools
        self._reporter = reporter
        self._control = control
        self._tool_calls = tool_calls
        self._clock = clock
        self._provider = provider

    @property
    def run_id(self) -> str:
        """The id of the run being executed."""
        return self._run.id

    @property
    def workload(self) -> str:
        """The name of the workload being executed."""
        return self._workload.name

    @property
    def task(self) -> dict[str, JsonValue]:
        """The submitted task payload."""
        return dict(self._run.task)

    @property
    def sandbox(self) -> bool:
        """Whether the run is executing in a sandbox context."""
        return self._sandbox

    @property
    def model_identity(self) -> str | None:
        """The model identity bound to the run."""
        return self._run.model_identity

    def tool_call(
        self,
        tool_id: str,
        *,
        action_class: ActionClass | None = None,
        data_sensitivity: DataSensitivity = DataSensitivity.INTERNAL,
        host: str | None = None,
        output: str | None = None,
    ) -> ToolCallResult:
        """Route a tool call through the policy, egress, and shaping boundary."""
        result = self._tools.invoke(
            self._run.id,
            ToolCallRequest(
                tool_id=tool_id,
                action_class=action_class,
                data_sensitivity=data_sensitivity,
                host=host,
                output=output,
            ),
        )
        self._tool_calls.append(result)
        if result.outcome is ToolCallOutcome.DENIED:
            raise ToolCallDeniedError(result)
        if result.outcome is ToolCallOutcome.BLOCKED_INJECTION:
            raise ToolCallBlockedError(result)
        if result.outcome is ToolCallOutcome.ESCALATED:
            raise ToolCallEscalatedError(result)
        return result

    def complete(
        self,
        prompt: str | list[Message],
        *,
        temperature: float | None = None,
        max_tokens: int | None = None,
    ) -> CompletionResult:
        """Invoke the bound model through the governed provider seam (M23, #115)."""
        provider = self._provider or _default_provider()
        messages = (
            [Message(role="user", content=prompt)] if isinstance(prompt, str) else list(prompt)
        )
        bound = self._bound_model_identity()
        if bound is None:
            raise ValueError("no model identity is bound to this run or workload")
        request = CompletionRequest(
            messages=messages,
            model=bound,
            temperature=0.0 if temperature is None else temperature,
            max_tokens=max_tokens,
        )
        self.checkpoint()
        with telemetry.span(
            "model_call",
            run=self._run,
            workload=self._workload,
            attributes={"model_identity": bound},
        ) as active:
            response = provider.complete(request)
            self.checkpoint()
            try:
                canonical = validate_model_identity(response.model_identity)
            except ValueError:
                self._record_identity_mismatch(bound, response.model_identity)
                raise ModelIdentityMismatchError(bound, response.model_identity) from None
            if canonical != bound:
                self._record_identity_mismatch(bound, canonical)
                raise ModelIdentityMismatchError(bound, canonical)
            active.set_attribute("model_identity", canonical)
            active.set_attribute("input_tokens", response.usage.input_tokens)
            active.set_attribute("output_tokens", response.usage.output_tokens)
            active.set_attribute("cost_usd", 0.0)
            active.set_attribute("finish_reason", response.finish_reason)
        report = UsageReport(
            run_id=self._run.id,
            input_tokens=response.usage.input_tokens,
            output_tokens=response.usage.output_tokens,
            tool_calls=0,
            cost_usd=0.0,
            timestamp=self._clock(),
            model_identity=canonical,
        )
        updated = self._reporter.record_usage(self._run.id, report)
        if updated.state in _TERMINAL:
            raise RunTerminatedError(updated.state)
        return CompletionResult(
            content=response.content,
            model_identity=canonical,
            usage=TokenUsage(
                input_tokens=response.usage.input_tokens,
                output_tokens=response.usage.output_tokens,
            ),
            finish_reason=response.finish_reason,
        )

    def _bound_model_identity(self) -> str | None:
        if self._run.model_identity is not None:
            return self._run.model_identity
        identity = self._workload.spec.model.identity
        if identity is None:
            return None
        return canonical_model_identity(identity)

    def _record_identity_mismatch(self, expected: str, actual: str) -> None:
        metrics.get_metrics().record_model_swap_block(workload=self._workload.name)
        self._reporter.record_event(
            self._run.id,
            EventType.POLICY_DECISION,
            "adapter",
            detail=f"security: model_identity_mismatch expected={expected!r} actual={actual!r}",
        )

    def report_usage(
        self,
        *,
        input_tokens: int = 0,
        output_tokens: int = 0,
        tool_calls: int = 0,
        cost_usd: float = 0.0,
    ) -> None:
        """Report usage for server-side pricing and budget enforcement."""
        report = UsageReport(
            run_id=self._run.id,
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            tool_calls=tool_calls,
            cost_usd=cost_usd,
            timestamp=self._clock(),
            model_identity=self._run.model_identity,
        )
        updated = self._reporter.record_usage(self._run.id, report)
        if updated.state in _TERMINAL:
            raise RunTerminatedError(updated.state)

    def checkpoint(self) -> None:
        """Yield control: block while paused, raise when cancelled."""
        self._control.checkpoint()

    @property
    def tool_calls(self) -> list[ToolCallResult]:
        """The tool calls routed through the boundary so far."""
        return list(self._tool_calls)
