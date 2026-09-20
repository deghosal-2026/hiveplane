"""Escalation flow: trigger evaluation, model invocation, fail-open.

This module implements the escalation pipeline described in SPEC §4:

*   EscalationManager (SPEC §4.1) — full orchestration: cap check,
    package, sanitize, redact, prompt building, model call, logging.

*   InterruptHandler (SPEC §4.2) — stdin/callback-based user prompt
    for on_escalate="interrupt" in raw Python (non-LangGraph) mode.

Key SPEC references:
    - §4.1 EscalationManager pseudocode → escalate() method below
    - §4.2 on_escalate modes → "auto" (default) and "interrupt"
    - §4.4 Default escalation prompt → _internal/prompts.py
    - §4.5 Fail-open (OQ-3) → _get_fail_open_output()
    - §4.6 Max escalations per run (OQ-4) → cap check in escalate()
    - §10  Threat model (T4, T6) → fail-open + cap enforcement
"""

import time
from typing import TYPE_CHECKING, Any

# EscalationManager depends on EscalationLogger for JSONL output and event hooks.
# The logger is created internally with the config's log_dir, or the user can
# pass a pre-configured logger for custom hook registration.
from ai_loopguard._internal.prompts import DEFAULT_ESCALATION_PROMPT
from ai_loopguard._internal.state import GuardState, TriggerResult
from ai_loopguard.config import GuardConfig
from ai_loopguard.context import ContextPackager, EscalationContext, Redactor, Sanitizer
from ai_loopguard.exceptions import (
    ContextError,
    EscalationError,
)
from ai_loopguard.logging import (
    CappedEvent,
    EscalationEvent,
    EscalationLogger,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ai_loopguard.logging import EventHook


class InterruptHandler:
    """Raw Python interrupt handler for on_escalate="interrupt".

    When the user configures on_escalate="interrupt" and the agent loop
    is not inside a LangGraph graph (which uses its native interrupt()),
    loopguard uses this handler to pause execution and ask the user
    whether to escalate.

    Two modes:
        1. Default: prompts via stdin (input()).
        2. Custom: the user provides an interrupt_callback callable that
           receives the context summary and returns "y" or "n".

    Attributes:
        _callback: Optional user-provided callable.  If None, falls back
            to stdin via input().

    """

    def __init__(
        self,
        callback: "Callable[[str], str] | None" = None,
    ) -> None:
        """Initialise the interrupt handler.

        Args:
            callback: Optional user-provided callable that receives the
                context summary string and must return "y" (escalate)
                or "n" (skip escalation).

        """
        self._callback = callback

    def prompt(self, context_summary: str) -> bool:
        """Prompt the user and return True if escalation should proceed.

        Displays a formatted prompt with the context summary and the
        escalation model details, then waits for a yes/no response.

        Args:
            context_summary: Human-readable summary of what triggered
                the escalation and the state of the loop.

        Returns:
            True if the user wants to escalate, False if they decline.

        """
        message = (
            f"\n{'=' * 60}\n"
            f"loopguard interrupt — agent is stuck\n"
            f"{'=' * 60}\n"
            f"{context_summary}\n"
            f"{'=' * 60}\n"
            f"Escalate to stronger model? [y/n]: "
        )

        if self._callback is not None:
            response = self._callback(message)
        else:
            response = input(message)

        # Case-insensitive, whitespace-stripped.  Only "y" counts as yes.
        # "yes", "Y", " y " all work; "n", "", Ctrl-C all skip escalation.
        return response.strip().lower() == "y"

    async def prompt_async(self, context_summary: str) -> bool:
        """Non-blocking async version of prompt().

        When on_escalate="interrupt" is used with async code (e.g.,
        @guard.aprotect), this method avoids blocking the event loop
        by running the callback in a thread pool or awaiting an async
        callback.  Addresses #40 — sync input() blocks the event loop.

        Args:
            context_summary: Human-readable summary from ContextPackager.

        Returns:
            True if the user wants to escalate, False if they decline.

        """
        message = (
            f"\n{'=' * 60}\n"
            f"loopguard interrupt — agent is stuck\n"
            f"{'=' * 60}\n"
            f"{context_summary}\n"
            f"{'=' * 60}\n"
            f"Escalate to stronger model? [y/n]: "
        )

        if self._callback is not None:
            import inspect

            # Dispatch: async callback → await directly.  Sync callback →
            # run in thread pool to avoid blocking the event loop (issue #40).
            # No callback → run input() in thread pool for the same reason.
            if inspect.iscoroutinefunction(self._callback):
                response = await self._callback(message)
            else:
                import asyncio

                response = await asyncio.to_thread(
                    self._callback, message
                )
        else:
            import asyncio

            response = await asyncio.to_thread(input, message)

        return str(response).strip().lower() == "y"


class EscalationManager:
    """Handles the full escalation flow when a trigger fires.

    Orchestrates: cap check → packaging → sanitization → redaction →
    prompt building → model invocation → logging → return.

    The escalate() method mirrors SPEC §4.1 pseudocode exactly.
    escalate_async() provides async model invocation for @guard.aprotect.

    Key design decisions:
        - Fail-open (OQ-3): configurable via on_guard_error.  The agent
          loop must never crash because of loopguard internal errors
          (PRD §9.3).
        - Escalation cap (OQ-4): max_escalations_per_run defaults to 1,
          configurable up to 10.  Prevents infinite escalation loops
          and unbounded cost (T6).
        - Context pipeline: package → sanitize → redact.  Order matters:
          sanitization wraps content in delimiters first, then redaction
          strips secrets from the delimited content.
    """

    def __init__(
        self,
        config: GuardConfig,
        state: GuardState,
        logger: EscalationLogger | None = None,
        interrupt_callback: "Callable[[str], str] | None" = None,
    ) -> None:
        """Initialise the escalation manager.

        Args:
            config: The full GuardConfig controlling thresholds, prompts,
                and fail-open behaviour.
            state: The per-execution GuardState (shared with the detector).
            logger: An EscalationLogger for JSONL + event hooks.  Created
                if not provided.
            interrupt_callback: Optional callback for interrupt mode
                (on_escalate="interrupt").

        """
        self._config = config
        self._state = state
        self._logger = logger or EscalationLogger(log_dir=config.log_dir)
        self._packager = ContextPackager(
            max_context_tokens=config.max_context_tokens,
            compress_context=config.compress_context,
        )
        self._interrupt = InterruptHandler(callback=interrupt_callback)

    @property
    def logger(self) -> EscalationLogger:
        """Expose the logger for external hook registration."""
        return self._logger

    def register_hook(self, hook: "EventHook") -> None:
        """Register a pluggable observability hook.

        Convenience wrapper around EscalationLogger.register_hook().

        Args:
            hook: An EventHook implementation.

        """
        self._logger.register_hook(hook)

    # ── Main escalation entry point ─────────────────────────────────────

    def escalate(self, trigger_result: TriggerResult) -> Any:  # noqa: ANN401
        """Full synchronous escalation flow.

        SPEC §4.1 excerpt::
            1. Check escalation cap
            2. Package context
            3. Sanitize
            4. Redact
            5. Build escalation prompt
            6. Call escalation model
            7. Log event
            8. Return escalated output

        Args:
            trigger_result: Which trigger fired and why.

        Returns:
            The escalation model's output if escalation succeeds, or the
            fail-open output if escalation fails or is capped.

        Raises:
            EscalationError: Only if on_guard_error="raise_original" and
                the last original error exists.

        """
        # 1. Cap check MUST come before interrupt check.  If the cap is
        # already reached, there is no point asking the user — escalation
        # is impossible.  This check is on self._state (shared), not a
        # local counter.
        # The cap prevents uncontrolled cost spikes (T6 mitigation):
        # even if triggers keep firing, the escalation model is not called
        # more than max_escalations_per_run times.
        if self._state.escalation_cap_reached(
            self._config.max_escalations_per_run
        ):
            return self._handle_cap(trigger_result)

        # 2. Interrupt check (if on_escalate="interrupt")
        # The interrupt callback receives a human-readable summary via
        # ContextPackager.summary() before the full context is built.
        # This avoids token waste — if the user says "no", we never
        # package/sanitize/redact the full context.
        if self._config.on_escalate == "interrupt":
            context = self._package_context(trigger_result)
            summary = self._packager.summary(context)
            proceed = self._interrupt.prompt(summary)
            if not proceed:
                return self._get_last_output()

        # Outer try/except catches escalation model failures and triggers
        # fail-open behaviour (OQ-3).  The three fail-open modes are:
        #   - raise_original: re-raise the last agent error
        #   - return_last_output: return last successful output
        #   - return_sentinel: return the configured sentinel value
        # This ensures the agent loop never crashes due to guard failures.
        try:
            return self._do_escalate(trigger_result)
        except Exception as exc:
            return self._handle_escalation_failure(trigger_result, exc)

    async def escalate_async(self, trigger_result: TriggerResult) -> Any:  # noqa: ANN401
        """Full asynchronous escalation flow.

        Mirrors escalate() but uses await for the escalation model call.
        Used by @guard.aprotect.

        Args:
            trigger_result: Which trigger fired and why.

        Returns:
            The escalation model's output or fail-open output.

        """
        # 1. Cap check (T6 / OQ-4) — thread-safe via GuardState
        if self._state.escalation_cap_reached(
            self._config.max_escalations_per_run
        ):
            return self._handle_cap(trigger_result)

        # 2. Interrupt check (async — uses prompt_async, #40)
        if self._config.on_escalate == "interrupt":
            context = self._package_context(trigger_result)
            summary = self._packager.summary(context)
            proceed = await self._interrupt.prompt_async(summary)
            if not proceed:
                return self._get_last_output()

        try:
            return await self._do_escalate_async(trigger_result)
        except Exception as exc:
            return self._handle_escalation_failure(trigger_result, exc)

    # ── Internal pipeline steps ─────────────────────────────────────────

    def _do_escalate(self, trigger_result: TriggerResult) -> Any:  # noqa: ANN401
        """Execute the escalation model call and logging (sync).

        Assumes cap and interrupt checks have already passed.
        This method is the shared backbone for both escalate() and
        escalate_async() minus the async/await difference.
        """
        # 2-4. Package → Sanitize → Redact
        context = self._package_context(trigger_result)

        # 5. Build escalation prompt
        prompt = self._build_prompt(context, trigger_result)

        # 6. Call escalation model (T4: let failures propagate to
        # escalate()'s outer handler — avoids double logging, #29)
        # escalation_model is typed as Any in GuardConfig so langchain-core
        # is not a required dependency.  invoke() is duck-typed.
        escalation_model = self._config.escalation_model
        response = escalation_model.invoke(prompt)
        # success=True after a successful model call.  If the call raises,
        # the exception propagates to escalate()'s outer handler, which
        # calls _handle_escalation_failure — no double-logging.
        success = True

        # 7. Increment BEFORE logging so external hooks see the updated
        # count.  Log escalation event — increment atomically (PRD §9.3).
        self._state.increment_escalation_count()
        self._log_escalation_event(
            trigger_result=trigger_result,
            context=context,
            response=response,
            success=success,
        )

        # 8. Return escalated output (unwrapped from AIMessage.content)
        return self._extract_content(response)

    async def _do_escalate_async(self, trigger_result: TriggerResult) -> Any:  # noqa: ANN401
        """Execute the escalation model call and logging (async).

        Mirrors _do_escalate() with async model invocation.  Cap and
        interrupt checks are handled by escalate_async() before this
        method is called.
        """
        # 2-4. Package → Sanitize → Redact
        context = self._package_context(trigger_result)

        # 5. Build escalation prompt
        prompt = self._build_prompt(context, trigger_result)

        # 6. Call escalation model (async)
        # T4: let failures propagate to escalate_async()'s outer handler
        escalation_model = self._config.escalation_model
        response = await escalation_model.ainvoke(prompt)
        success = True

        # 7. Log escalation event — increment atomically (PRD §9.3)
        self._state.increment_escalation_count()
        self._log_escalation_event(
            trigger_result=trigger_result,
            context=context,
            response=response,
            success=success,
        )

        return self._extract_content(response)

    # ── Context pipeline (steps 2-4) ────────────────────────────────────

    def _package_context(self, trigger_result: TriggerResult) -> EscalationContext:
        """Run the full context pipeline: package → sanitize → redact.

        This encapsulates steps 2-4 from SPEC §4.1.  Sanitization and
        redaction are controlled by GuardConfig flags (sanitize_context,
        redact_patterns, redact_fields).

        Args:
            trigger_result: Which trigger fired and why.

        Returns:
            A packaged, sanitized, and redacted EscalationContext.

        """
        context = self._packager.package(self._state, trigger_result)

        if self._config.sanitize_context:
            context = Sanitizer.sanitize(context)

        if self._config.redact_patterns or self._config.redact_fields:
            context = Redactor.redact(
                context,
                patterns=self._config.redact_patterns,
                fields=self._config.redact_fields,
                use_builtin=True,
            )

        return context

    # ── Prompt building (step 5) ────────────────────────────────────────

    def _build_prompt(
        self,
        context: EscalationContext,
        trigger_result: TriggerResult,
    ) -> str:
        """Build the escalation prompt from config or default template.

        Uses GuardConfig.escalation_prompt if set, otherwise the default
        template from _internal/prompts.py (SPEC §4.4).

        The prompt template supports these placeholders:
            {trigger_type}, {trigger_detail}, {retry_count},
            {workhorse_model}, {failed_attempts}, {last_error}

        Args:
            context: The packaged/sanitized/redacted EscalationContext.
            trigger_result: Which trigger fired.

        Returns:
            A fully-formatted prompt string ready for the model.

        """
        template = self._config.escalation_prompt or DEFAULT_ESCALATION_PROMPT

        failed_attempts_str = self._format_failed_attempts(
            context.failed_attempts
        )

        last_error = context.last_error or "No error recorded."
        workhorse_model = (
            self._config.workhorse_model_name or "unknown"
        )

        try:
            return template.format(
                trigger_type=trigger_result.trigger_name,
                trigger_detail=trigger_result.detail,
                retry_count=trigger_result.retry_count,
                workhorse_model=workhorse_model,
                failed_attempts=failed_attempts_str,
                last_error=last_error,
            )
        except (KeyError, ValueError) as exc:
            raise ContextError(
                f"Escalation prompt template error: {exc}"
            ) from exc

    def _format_failed_attempts(
        self,
        attempts: list[dict[str, Any]],
    ) -> str:
        """Format failed attempt summaries into a readable string block.

        Each attempt is indented and prefixed with its step number.
        Compressed summaries (dicts with a "_summary" key) are rendered
        as-is without step numbering.

        Args:
            attempts: List of step summary dicts from ContextPackager.

        Returns:
            A multi-line string suitable for insertion into the prompt.

        """
        lines: list[str] = []
        for attempt in attempts:
            if "_summary" in attempt:
                lines.append(f"- [Summary] {attempt['_summary']}")
            else:
                step = attempt.get("step", "?")
                error = attempt.get("error", "no error")
                err_msg = attempt.get("error_message", "")
                output = attempt.get("output", "no output")
                lines.append(
                    f"- Step {step}: error={error}"
                    + (f" ({err_msg[:120]})" if err_msg else "")
                    + f" | output={str(output)[:120]}"
                )
        if not lines:
            return "No failed attempts recorded."
        return "\n".join(lines)

    # ── Model response extraction ───────────────────────────────────────

    def _extract_content(self, response: Any) -> Any:  # noqa: ANN401
        """Extract the text content from a LangChain response.

        LangChain BaseChatModel.invoke() returns an AIMessage with a
        .content attribute.  We unwrap this for convenience so the
        caller gets a plain string or raw output.

        If the response is already a plain string (e.g., from a wrapper
        or mock), return it as-is.

        Args:
            response: The return value from escalation_model.invoke().

        Returns:
            The extracted text content.

        """
        # Try to unwrap AIMessage.content.  If the response is already a
        # plain string (mock, non-LangChain wrapper), return as-is.
        # try/except avoids importing langchain-core at module level.
        try:
            return response.content
        except AttributeError:
            return response

    # ── Cap handling (T6 / OQ-4) ───────────────────────────────────────

    def _handle_cap(self, trigger_result: TriggerResult) -> Any:  # noqa: ANN401
        """Handle escalation cap reached — log and return last output.

        SPEC §4.6: when max_escalations_per_run is hit, log a capped
        event and return the last known output.

        Args:
            trigger_result: Which trigger tried to fire.

        Returns:
            The last step's output, or None if no steps exist.

        """
        event = CappedEvent(
            timestamp=time.time(),
            trigger_type=trigger_result.trigger_name,
            trigger_detail=trigger_result.detail,
            retry_count=trigger_result.retry_count,
        )
        self._logger.log_capped(event)
        return self._get_last_output()

    # ── Fail-open (SPEC §4.5, OQ-3) ─────────────────────────────────────

    def _get_fail_open_output(self) -> Any:  # noqa: ANN401
        """Return the configured fail-open output.

        Three modes per SPEC §4.5:
            - "raise_original": re-raise the last original error.
            - "return_last_output": return the last successful output.
            - "return_sentinel": return the configured sentinel_value.

        Returns:
            Output or None.  Raises in "raise_original" mode.

        Raises:
            EscalationError: If mode is "raise_original" and there is
                a last original error to re-raise.

        """
        # Fail-open modes ensure the agent loop survives guard failures.
        # "raise_original" is the default — the user sees the original
        # error as if loopguard never intercepted it.  "return_last_output"
        # and "return_sentinel" are useful in production where you want
        # the agent to continue even on guard errors.
        mode = self._config.on_guard_error

        if mode == "raise_original":
            last = self._state.steps[-1] if self._state.steps else None
            if last and last.error:
                raise last.error
            raise EscalationError(
                "Escalation failed and no previous error to re-raise."
            )

        if mode == "return_last_output":
            return self._get_last_output()

        # mode == "return_sentinel"
        return self._config.sentinel_value

    def _get_last_output(self) -> Any:  # noqa: ANN401
        """Return the output of the last recorded step, or None."""
        if self._state.steps:
            return self._state.steps[-1].output
        return None

    # ── Logging ─────────────────────────────────────────────────────────

    def _log_escalation_event(
        self,
        trigger_result: TriggerResult,
        context: EscalationContext,
        response: Any,  # noqa: ANN401
        success: bool,
    ) -> None:
        """Construct and log an EscalationEvent.

        Args:
            trigger_result: Which trigger fired.
            context: The packaged/sanitized/redacted context.
            response: The escalation model's response object.
            success: Whether the escalation call succeeded.

        """
        escalation_model_name = getattr(
            self._config.escalation_model,
            "model_name",
            "unknown",
        )
        workhorse_model = (
            self._config.workhorse_model_name or "unknown"
        )

        escalation_tokens, escalation_cost = (
            self._extract_response_metrics(response)
        )

        event = EscalationEvent(
            timestamp=time.time(),
            trigger_type=trigger_result.trigger_name,
            trigger_detail=trigger_result.detail,
            retry_count=trigger_result.retry_count,
            workhorse_model=workhorse_model,
            escalation_model=escalation_model_name,
            # "failover" = same task failed repeatedly (model reliability).
            # "routing" = structured check (test/schema/custom) routed to
            # a stronger model (task complexity).  Helps analyse escalation
            # patterns in observability dashboards.
            escalation_category=(
                "failover"
                if trigger_result.trigger_name == "repeated_error"
                else "routing"
            ),
            context_tokens=self._estimate_tokens(context),
            escalation_tokens=escalation_tokens,
            escalation_cost_usd=escalation_cost,
            total_task_cost_usd=self._state.total_cost_usd,
            total_task_tokens=self._state.total_tokens,
            success=success,
            sanitized=self._config.sanitize_context,
            redacted_fields=list(self._config.redact_fields),
        )
        self._logger.log(event)

    def _handle_escalation_failure(
        self,
        trigger_result: TriggerResult,
        error: Exception,
    ) -> Any:  # noqa: ANN401
        """Log an escalation model failure and trigger fail-open.

        This is called from both escalate() and escalate_async() when
        the escalation model itself errors (T4 mitigation).  The error
        is logged and the configured fail-open behaviour determines
        what is returned.

        Args:
            trigger_result: Which trigger fired.
            error: The exception that occurred.

        Returns:
            The fail-open output per on_guard_error config.

        """
        # EscalationLogger.log_escalation_failure writes a FailOpenEvent
        # to the JSONL sink and dispatches to all registered EventHook
        # implementations (e.g., OTel, Datadog).  This gives observability
        # into guard failures without raising exceptions.
        self._logger.log_escalation_failure(
            error,
            CappedEvent(
                timestamp=time.time(),
                trigger_type=trigger_result.trigger_name,
                trigger_detail=trigger_result.detail,
                retry_count=trigger_result.retry_count,
            ),
            fail_open_mode=self._config.on_guard_error,
        )
        return self._get_fail_open_output()

    # ── Token estimation ────────────────────────────────────────────────

    def _estimate_tokens(self, content: Any) -> int:  # noqa: ANN401
        """Estimate token count using 1 token ≈ 4 characters.

        This is a rough heuristic used as a fallback when the model
        response does not include token usage metadata.  When available,
        actual token counts from response_metadata are preferred (#39).

        Args:
            content: Any serializable value (str, dict, object with .content).

        Returns:
            Estimated token count (minimum 1).

        """
        serialized = str(content)
        return max(1, len(serialized) // 4)

    def _extract_response_metrics(
        self,
        response: Any,  # noqa: ANN401
    ) -> tuple[int, float]:
        """Extract token count and cost from model response metadata.

        Tries to parse langchain-core AIMessage.response_metadata for
        token usage info. Falls back to heuristic estimate if metadata
        is not available.  Addresses #32 (cost always 0.0) and #39
        (token heuristic).

        Args:
            response: The raw model response (AIMessage or similar).

        Returns:
            A tuple of (tokens_used, cost_usd).  Cost is 0.0 if the
            pricing model is unknown.

        """
        metadata: dict[str, Any] = getattr(response, "response_metadata", {})
        if not metadata:
            return self._estimate_tokens(response), 0.0

        usage: dict[str, int] = metadata.get("token_usage", {})
        if usage:
            total_tokens = usage.get("total_tokens", 0)
            if not total_tokens:
                total_tokens = usage.get("completion_tokens", 0)
            return total_tokens, 0.0

        return self._estimate_tokens(response), 0.0
