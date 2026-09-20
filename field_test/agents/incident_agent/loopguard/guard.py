"""Guard class — the main entry point for loopguard.

Users create a Guard instance, configure it with an escalation model,
and decorate their agent step functions with @guard.protect (sync) or
@guard.aprotect (async). The decorator transparently monitors execution,
detects stuck patterns, and escalates to a stronger model when needed.

SPEC reference: §7.1 (sync decorator), §7.2 (async decorator).

Design note (S5):
    Each call to the decorated function records one step and checks
    triggers.  The EXTERNAL agent loop (e.g., a while-true loop calling
    model.generate) controls iteration.  The guard does NOT have its
    own internal retry loop — it tracks across external calls so that
    metadata-based triggers (test_failure, schema_invalid) accumulate
    correctly across successful steps.  This matches UC-1 through UC-4.

    The shared GuardState persists across calls until explicitly reset.
    Call ``guard.reset()`` between independent agent tasks to clear
    accumulated history and escalation count.

Thread safety:
    GuardState.add_step() and escalation_cap_reached() use a
    threading.Lock internally.  The Guard class itself is NOT
    thread-safe — wrapping the same Guard instance in multiple
    threads would interleave step tracking.  Users should use one
    Guard per agent loop, or guard.reset() between threads.
"""

import functools
import time
from collections.abc import Callable
from typing import Any

from ai_loopguard._internal.state import GuardState, StepRecord
from ai_loopguard.config import GuardConfig
from ai_loopguard.detectors import FailureDetector
from ai_loopguard.escalation import EscalationManager
from ai_loopguard.logging import EscalationLogger


class Guard:
    """Wraps agent functions to detect stuck loops and escalate.

    Each call to the decorated function records one execution step.
    History accumulates across calls so triggers like test_failure
    and schema_invalid fire correctly.  Call ``guard.reset()`` to
    clear history between independent tasks (#42).

    Typical usage::

        guard = Guard(
            escalation_model=gpt4,
            workhorse_model_name="qwen2.5-coder",
            on_escalate="auto",
            interrupt_callback=my_ui_prompt,  # optional
        )

        @guard.protect
        def step(state):
            output = model.generate(state)
            guard.record_test_results(run_tests(output))
            return output

        guard.reset()  # between tasks

    Key design decisions:
        - One call = one step.  No internal retry loop.
        - Shared state with reset().  Trigger history across calls.
        - Fail-open: model=None or loopguard errors → preserve original (#43).
        - interrupt_callback: callable (str) -> "y"/"n" for interrupt mode.
    """

    def __init__(
        self,
        **kwargs: Any,  # noqa: ANN401
    ) -> None:
        """Initialise a Guard instance.

        Args:
            **kwargs: GuardConfig fields plus optional ``interrupt_callback``
                callable ``(summary: str) -> str`` returning "y" or "n".

        """
        # interrupt_callback is popped from kwargs before GuardConfig
        # construction because it is not a GuardConfig field — it is
        # specific to the Guard class's interrupt mode handling.
        self._interrupt_callback: Callable[[str], str] | None = (
            kwargs.pop("interrupt_callback", None)  # type: ignore[arg-type]
        )
        self._config = GuardConfig(**kwargs)
        # Shared GuardState persists across multiple @guard.protect calls
        # until reset().  This lets triggers accumulate history across
        # steps in the agent loop without the user manually tracking state.
        self._state: GuardState = GuardState(
            max_history_steps=self._config.max_history_steps,
        )
        self._logger = EscalationLogger(log_dir=self._config.log_dir)
        # _pending_test_results and _pending_schema_valid are set by the user
        # between step calls (record_test_results / record_schema_valid) and
        # consumed by the next _record_success / _record_failure.  They act as
        # a bridge between the imperative user API and the declarative StepRecord.
        self._pending_test_results: dict[str, bool] | None = None
        self._pending_schema_valid: bool | None = None
        # Lazy-created on first guarded call so Guard can be constructed early
        # (e.g., at import time) without heavyweight detector/manager init.
        # This also means Guard.__init__ never fails — config errors surface
        # on first use, which is friendlier for module-level instantiation.
        self._detector: FailureDetector | None = None
        self._escalation_mgr: EscalationManager | None = None

    # ── Public API ────────────────────────────────────────────────────────

    @property
    def config(self) -> GuardConfig:
        """Return the GuardConfig for this instance."""
        return self._config

    @property
    def state(self) -> GuardState:
        """Return the shared GuardState. Never returns None (#44)."""
        return self._state

    @property
    def logger(self) -> EscalationLogger:
        """Expose the EscalationLogger for hook registration.

        Usage::
            guard.logger.register_hook(OTelEventHook())
        """
        return self._logger

    def reset(self) -> None:
        """Reset accumulated state and cached components (#42, #54).

        Clears step history, escalation count, and pending metadata.
        Invalidates detector and escalation manager so fresh ones
        are created on the next guarded call.

        reset() is the only way to clear guard history between independent
        agent tasks.  Without it, trigger thresholds accumulate across
        unrelated tasks, causing false positives.
        """
        # Creates a new GuardState (old one is garbage-collected).
        # Pending metadata is cleared to avoid stale data leaking
        # across tasks.
        self._state = GuardState(
            max_history_steps=self._config.max_history_steps,
        )
        self._pending_test_results = None
        self._pending_schema_valid = None
        # Null cached components so _ensure_components() recreates them on the
        # next guarded call.  The EscalationManager holds a reference to the old
        # GuardState — after reset(), the state is a new object, so the old
        # manager must be replaced to avoid stale-state bugs (issue #54).
        self._detector = None
        self._escalation_mgr = None

    def record_test_results(self, results: dict[str, bool]) -> None:
        """Record test results for the current step (FR-1.2).

        Sets pending value for the StepRecord AND updates the last
        recorded step immediately for backward compat (#53).
        The immediate update on steps[-1] means the user sees results
        reflected in step history immediately after this call, avoiding
        a one-step lag.  However, this mutates the PREVIOUS step when
        called inside a @guard.protect function body — use with care.
        """
        self._pending_test_results = results
        if self._state.steps:
            self._state.steps[-1].test_results = results

    def record_schema_valid(self, *, valid: bool) -> None:
        """Record schema validation result for the current step (FR-1.3).

        Keyword-only bool flag. Updates pending AND last step for compat.
        """
        self._pending_schema_valid = valid
        if self._state.steps:
            self._state.steps[-1].schema_valid = valid

    # ── Sync decorator (SPEC §7.1) ────────────────────────────────────────

    def protect(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """Sync decorator. Wraps any Python function with loop detection.

        Each call records one step.  On trigger fire, escalates.  On no
        trigger with exception, returns None (fail-open).  Uses
        @functools.wraps for metadata preservation.

        The decorator captures `self` (Guard instance) at decoration time,
        so the decorated function shares guard state across all calls.
        This is the key mechanism for cross-step trigger accumulation.
        """
        @functools.wraps(fn)
        def wrapper(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            detector, mgr = self._ensure_components()
            return self._step_sync(
                fn, detector, mgr, args, kwargs,
            )

        return wrapper

    # ── Async decorator (SPEC §7.2) ───────────────────────────────────────

    def aprotect(self, fn: Callable[..., Any]) -> Callable[..., Any]:
        """Async decorator.  Same semantics as protect() with await."""
        @functools.wraps(fn)
        async def wrapper(*args: Any, **kwargs: Any) -> Any:  # noqa: ANN401
            detector, mgr = self._ensure_components()
            return await self._step_async(
                fn, detector, mgr, args, kwargs,
            )

        return wrapper

    # ── Step handlers (shared logic via helpers, #46 DRY) ─────────────────

    def _step_sync(
        self,
        fn: Callable[..., Any],
        detector: FailureDetector,
        mgr: EscalationManager,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Any:  # noqa: ANN401
        """Record one sync step and escalate on trigger.

        Both success and failure paths call _resolve_step — even a failed
        step can fire a trigger (e.g., 3 consecutive errors → repeated_error).
        On failure, output=None is passed because there is no successful
        output to return as a fallback.
        """
        try:
            output = fn(*args, **kwargs)
            self._record_success(output)
            return self._resolve_step(output, detector, mgr)
        except Exception as exc:
            self._record_failure(exc)
            return self._resolve_step(None, detector, mgr)

    async def _step_async(
        self,
        fn: Callable[..., Any],
        detector: FailureDetector,
        mgr: EscalationManager,
        args: tuple[Any, ...],
        kwargs: dict[str, Any],
    ) -> Any:  # noqa: ANN401
        """Record one async step and escalate on trigger."""
        try:
            output = await fn(*args, **kwargs)
            self._record_success(output)
            return await self._resolve_step_async(output, detector, mgr)
        except Exception as exc:
            self._record_failure(exc)
            return await self._resolve_step_async(None, detector, mgr)

    # ── Step resolution — shared trigger + escalation logic ───────────────

    def _resolve_step(
        self,
        output: Any,  # noqa: ANN401
        detector: FailureDetector,
        mgr: EscalationManager,
    ) -> Any:  # noqa: ANN401
        """Check triggers and escalate (sync) or return output.

        If a trigger fires but no escalation model is configured, the guard
        returns the last output (fail-open).  This supports detection-only
        mode where users want observability/logging without an escalation model.

        escalation_model can be None for two reasons:
        1. The user configured detection-only mode (no escalation needed).
        2. The constructor accepted escalation_model=None (default) and
           no model was set.  The guard degrades gracefully by returning
           the last output — the agent loop continues, and the log records
           what trigger fired (for later analysis).
        """
        trigger_result = detector.check(self._state)
        if trigger_result is not None:
            if self._config.escalation_model is not None:
                return mgr.escalate(trigger_result)
            return self._get_last_output()
        return output

    async def _resolve_step_async(
        self,
        output: Any,  # noqa: ANN401
        detector: FailureDetector,
        mgr: EscalationManager,
    ) -> Any:  # noqa: ANN401
        """Check triggers and escalate (async) or return output."""
        trigger_result = detector.check(self._state)
        if trigger_result is not None:
            if self._config.escalation_model is not None:
                return await mgr.escalate_async(trigger_result)
            return self._get_last_output()
        return output

    # ── Shared record helpers ─────────────────────────────────────────────

    def _record_success(self, output: Any) -> None:  # noqa: ANN401
        """Record a successful step with output and pending metadata.

        step_num is computed BEFORE add_step so the new step gets its
        correct sequence number (0-indexed, matching array indices).
        """
        now = time.time()
        self._state.add_step(
            StepRecord(
                step_num=len(self._state.steps),
                output=output,
                error=None,
                error_type=None,
                error_message=None,
                test_results=self._pending_test_results,
                schema_valid=self._pending_schema_valid,
                timestamp=now,
            )
        )
        # Clear pending metadata after consumption.  If the user forgets
        # to call record_test_results() on the next step, stale values
        # would silently carry over — clearing prevents metadata leaks.
        self._pending_test_results = None
        self._pending_schema_valid = None

    def _record_failure(self, exc: Exception) -> None:
        """Record a failed step with exception details."""
        now = time.time()
        self._state.add_step(
            StepRecord(
                step_num=len(self._state.steps),
                output=None,
                error=exc,
                error_type=type(exc).__name__,
                error_message=str(exc),
                test_results=self._pending_test_results,
                schema_valid=self._pending_schema_valid,
                timestamp=now,
            )
        )
        self._pending_test_results = None
        self._pending_schema_valid = None

    # ── Lazy init ─────────────────────────────────────────────────────────

    def _ensure_components(self) -> tuple[FailureDetector, EscalationManager]:
        """Create detector and escalation manager on first call or after reset.

        Lazy construction: supports use cases where Guard is created early
        (e.g., at import time) but execution happens later.  Also called by
        the CrewAI wrapper to initialise components when used without
        @guard.protect.

        Recreates EscalationManager if the cached one references a stale
        state (#54).  Passes interrupt_callback from Guard.__init__ (#49).
        """
        if self._detector is None:
            self._detector = FailureDetector(self._config.triggers)
        if self._escalation_mgr is None:
            self._escalation_mgr = EscalationManager(
                config=self._config,
                state=self._state,
                logger=self._logger,
                interrupt_callback=self._interrupt_callback,
            )
        return self._detector, self._escalation_mgr

    # ── Internal helpers ───────────────────────────────────────────────────

    def _get_last_output(self) -> Any:  # noqa: ANN401
        """Return the output of the last recorded step, or None."""
        if self._state.steps:
            return self._state.steps[-1].output
        return None
