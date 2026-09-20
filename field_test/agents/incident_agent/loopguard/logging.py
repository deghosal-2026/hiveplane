"""Logging and event hooks for escalation events.

loopguard emits structured JSONL logs for every escalation, capped
escalation, and fail-open event. External backends (OpenTelemetry,
custom sinks) can plug in via the EventHook protocol.

JSONL format: each line is a single JSON object (EscalationEvent,
CappedEvent, or FailOpenEvent serialised via Pydantic's model_dump_json).
This makes logs greppable and importable by the CLI analyzer.
"""

from typing import IO, Literal, Protocol, runtime_checkable

from pydantic import BaseModel


class EscalationEvent(BaseModel):
    """Structured event emitted for every escalation.

    Contains the full picture: which trigger fired, how many retries,
    which models were involved, token/cost impact, and whether the
    escalation was successful. This is the primary log record.

    Fields:
        timestamp: Unix timestamp of the escalation.
        trigger_type: Which trigger fired ("repeated_error",
            "test_failure", "schema_invalid", or "custom").
        trigger_detail: Human-readable detail (e.g., "ValueError on
            line 42, 3 consecutive").
        retry_count: How many retries occurred before escalation.
        workhorse_model: Name of the model that got stuck.
        escalation_model: Name of the model that was escalated to.
        escalation_category: "routing" (intentional escalation) or
            "failover" (availability failure) — FR-3.3.
        context_tokens: Tokens in the packaged context sent to the
            escalation model.
        escalation_tokens: Tokens consumed by the escalation model call.
        escalation_cost_usd: Cost of the escalation model call.
        total_task_cost_usd: Total cost including retries + escalation.
        total_task_tokens: Total tokens including retries + escalation.
        success: Whether the escalation produced a valid result.
        sanitized: Whether context was sanitized (T1 mitigation applied).
        redacted_fields: List of field names that were redacted (T2).

    """

    timestamp: float
    trigger_type: str
    trigger_detail: str
    retry_count: int
    workhorse_model: str
    escalation_model: str
    escalation_category: Literal["routing", "failover"]
    context_tokens: int
    escalation_tokens: int
    escalation_cost_usd: float
    total_task_cost_usd: float
    total_task_tokens: int
    success: bool
    sanitized: bool
    redacted_fields: list[str]


class CappedEvent(BaseModel):
    """Event emitted when the escalation cap is reached.

    This means the escalation model was NOT called because
    max_escalations_per_run was already hit. The last known output
    is returned instead.

    Fields:
        timestamp: Unix timestamp when the cap was hit.
        trigger_type: Which trigger tried to fire.
        trigger_detail: Human-readable detail.
        retry_count: Retry count at the time of capping.
        message: Default description ("Escalation cap reached").

    """

    timestamp: float
    trigger_type: str
    trigger_detail: str
    retry_count: int
    message: str = "Escalation cap reached"


class FailOpenEvent(BaseModel):
    """Event emitted when loopguard fails open.

    The escalation model call itself failed, so loopguard fell back
    to a configured fail-open strategy (raise original error, return
    last output, or return a sentinel value).

    Fields:
        timestamp: Unix timestamp of the failure.
        trigger_type: Which trigger caused the escalation attempt.
        error_message: The exception message from the failed call.
        fail_open_mode: Which fail-open strategy was used
            ("raise_original", "return_last_output", "return_sentinel").

    """

    timestamp: float
    trigger_type: str
    error_message: str
    fail_open_mode: str


# EventHook is a Protocol class (structural subtyping), not an ABC.
# Users implement the three methods and pass their instance to
# register_hook().  @runtime_checkable allows isinstance() checks.
# The dispatch pattern is: for each event type, EscalationLogger calls
# all registered hooks' corresponding method.  This is a simple
# observer pattern — no async dispatch, no event bus.
@runtime_checkable
class EventHook(Protocol):
    """Pluggable observability backend interface (FR-3.5).

    Implement this protocol to integrate with custom logging, metrics,
    or monitoring systems. All three event types are dispatched to
    every registered hook.

    The built-in OTelEventHook (loopguard[otel]) implements this to
    emit OpenTelemetry spans. Users can write their own hooks for
    Datadog, Grafana, custom dashboards, etc.

    Usage::

        class MyHook(EventHook):
            def on_escalation(self, event: EscalationEvent) -> None:
                send_to_datadog(event)

        guard.logger.register_hook(MyHook())
    """

    def on_escalation(self, event: EscalationEvent) -> None:
        """Handle an escalation event.

        Args:
            event: The escalation event containing trigger, cost, and
                model details.

        """
        ...

    def on_capped(self, event: CappedEvent) -> None:
        """Handle a capped escalation event.

        Called when max_escalations_per_run prevents an escalation.

        Args:
            event: The capped event with trigger details.

        """
        ...

    def on_fail_open(self, event: FailOpenEvent) -> None:
        """Handle a fail-open event.

        Called when the escalation model itself fails and loopguard
        falls back to configured fail-open behaviour.

        Args:
            event: The fail-open event with error details.

        """
        ...


class EscalationLogger:
    """Emits structured JSONL logs and dispatches to event hooks.

    Every escalation, capped event, and fail-open is serialised as a
    JSONL line and sent to all registered EventHook implementations.

    Sink selection:
        - log_dir=None (default): writes JSONL to stdout
        - log_dir="/path/to/dir": writes JSONL to /path/to/dir/escalations.jsonl

    """

    # JSONL (JSON Lines) format chosen over structured logging libraries
    # because it is trivially grepable, importable by the CLI analyzer,
    # and compatible with log aggregation tools (Logstash, Datadog, etc.).
    # Each line is a complete JSON object — no multi-line parsing needed.
    def __init__(self, log_dir: str | None = None) -> None:
        """Initialise the logger and open the JSONL sink.

        Args:
            log_dir: Optional directory for JSONL log files.
                When None, logs are written to stdout.

        """
        self._log_dir = log_dir
        self._hooks: list[EventHook] = []
        # Open the sink: stdout or a file in log_dir.
        # Typed as IO[str] to cover both sys.stdout and file objects.
        # stdout sink is useful for development (logs appear inline with
        # agent output).  File sink is for production (persistent logs).
        sink: IO[str]
        if log_dir is not None:
            from pathlib import Path

            log_path = Path(log_dir) / "escalations.jsonl"
            log_path.parent.mkdir(parents=True, exist_ok=True)
            # Append mode: does not overwrite existing logs across restarts.
            sink = log_path.open("a", encoding="utf-8")
        else:
            import sys

            sink = sys.stdout
        self._sink: IO[str] | None = sink

    @property
    def hooks(self) -> list[EventHook]:
        """Return a copy of the registered hooks list (read-only view)."""
        return list(self._hooks)

    def register_hook(self, hook: EventHook) -> None:
        """Register a pluggable event hook.

        All future events (escalation, capped, fail-open) will be
        dispatched to this hook in addition to the JSONL sink.

        Args:
            hook: An EventHook implementation (e.g., OTelEventHook).

        """
        self._hooks.append(hook)

    # Every log method follows the same pattern:
    #   1. Serialise event to JSONL via Pydantic's model_dump_json
    #   2. Write to sink (file or stdout)
    #   3. Flush immediately (ensures logs survive crashes)
    #   4. Dispatch to all registered EventHook implementations
    # This ensures log durability and observability integration.
    def log(self, event: EscalationEvent) -> None:
        """Log an escalation event.

        Writes one JSONL line and dispatches to all registered hooks.

        Args:
            event: The escalation event to log.

        """
        line = event.model_dump_json()
        if self._sink:
            self._sink.write(line + "\n")
            self._sink.flush()
        for hook in self._hooks:
            hook.on_escalation(event)

    def log_capped(self, event: CappedEvent) -> None:
        """Log a capped escalation event.

        Called when max_escalations_per_run is reached.

        Args:
            event: The capped event to log.

        """
        line = event.model_dump_json()
        if self._sink:
            self._sink.write(line + "\n")
            self._sink.flush()
        for hook in self._hooks:
            hook.on_capped(event)

    def log_escalation_failure(
        self,
        error: Exception,
        event: CappedEvent | EscalationEvent,
        fail_open_mode: str = "raise_original",
    ) -> None:
        """Log an escalation model failure and trigger fail-open.

        Constructs a FailOpenEvent from the exception and dispatches
        it to the JSONL sink and all registered hooks.

        Both CappedEvent and EscalationEvent always have timestamp and
        trigger_type fields, so we access them directly.

        Args:
            error: The exception that occurred during model invocation.
            event: The original event that triggered the escalation.
            fail_open_mode: The actual on_guard_error config value
                (raise_original, return_last_output, return_sentinel).
                This fixes #35 — was previously hardcoded to "raise_original".

        """
        fail_open = FailOpenEvent(
            timestamp=event.timestamp,
            trigger_type=event.trigger_type,
            error_message=str(error),
            fail_open_mode=fail_open_mode,
        )
        line = fail_open.model_dump_json()
        if self._sink:
            self._sink.write(line + "\n")
            self._sink.flush()
        for hook in self._hooks:
            hook.on_fail_open(fail_open)

    def close(self) -> None:
        """Close the file sink if one is open."""
        if self._sink is not None and self._log_dir is not None:
            self._sink.close()
