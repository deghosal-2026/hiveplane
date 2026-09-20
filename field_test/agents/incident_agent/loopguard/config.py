"""Pydantic configuration models for loopguard.

GuardConfig holds all user-facing knobs: which triggers to use, how many
retries before escalation, how to handle failures, and security settings.
TriggerConfig controls a single trigger type's behaviour.

All validation is enforced by Pydantic at construction time — invalid
values raise ValidationError immediately, not at runtime.
"""

# Pydantic is used for validation (ge/le constraints, type coercions, field defaults).
# It catches misconfiguration at construction time rather than at runtime.
from collections.abc import Callable
from typing import Any, Literal

from pydantic import BaseModel, Field


class TriggerConfig(BaseModel):
    """Configuration for a single trigger type (repeated_error, test_failure, etc.).

    Attributes:
        enabled: Whether this trigger is active. Set to False to
            disable a trigger without removing it from the config.
        max_retries: Consecutive failures before the trigger fires.
            Must be between 1 and 20 (inclusive).
        custom_callback: Optional user-defined function that receives
            GuardState and returns True to fire this trigger. Used
            for the "custom" trigger type (FR-1.5).

    """

    # enabled=True by default so all triggers are active out of the box.
    # Setting enabled=False lets a trigger exist in config but be dormant —
    # useful for toggling triggers at runtime via config files.
    enabled: bool = True
    # ge/le constraints enforce FR-1.6: configurable retry thresholds.
    # ge=1: must fire on at least one failure (below 1 is meaningless).
    # le=20: safety bound — 20+ suggests misconfiguration or runaway retries.
    max_retries: int = Field(default=3, ge=1, le=20)
    # Typed as Callable[..., Any] because user callbacks have arbitrary
    # signatures.  Pydantic cannot validate callable signatures, so this
    # is a documentation hint only — the detector calls it as
    # custom_callback(state: GuardState) -> bool.
    custom_callback: Callable[..., Any] | None = None


class GuardConfig(BaseModel):
    """Top-level configuration for a Guard instance.

    Pass all of these as kwargs to Guard(escalation_model=..., ...) or
    construct directly and pass to Guard(config=...).

    Attributes:
        escalation_model: LangChain BaseChatModel to call when a trigger
            fires. Any BaseChatModel works: ChatOpenAI, ChatAnthropic,
            ChatOllama, OMLX adapters, etc. (FR-4.5).
        on_escalate: "auto" escalates immediately (default); "interrupt"
            asks the user before spending tokens (FR-2.7).
        escalation_prompt: Custom prompt template with {trigger_type},
            {trigger_detail}, etc. placeholders. None = use the default
            from _internal/prompts.py (FR-2.5).
        max_escalations_per_run: Hard cap on escalations per guarded
            call. Prevents unbounded cost (T6 mitigation). 1-10.
        triggers: Dict of trigger name -> TriggerConfig. Defaults to
            all three built-in triggers at max_retries=3.
        max_context_tokens: Token budget for escalation context packaging.
            The ContextPackager compresses to fit (FR-2.6). 500-32000.
        compress_context: Whether to compress failed attempts to a
            summary (first + last + middle summary) before escalation.
        redact_patterns: User-defined regex patterns for secret redaction.
            Applied before sending context to the escalation model (T2).
        redact_fields: Field names in agent state to strip entirely
            before escalation (e.g., ["api_key", "credentials"]).
        sanitize_context: Whether to wrap agent-produced content in
            delimiters to mitigate prompt injection (T1 / FR-2.8).
        on_guard_error: Fail-open behaviour when loopguard itself errors
            or escalation model fails:
            - "raise_original": re-raise the last original error (default)
            - "return_last_output": return the last successful output
            - "return_sentinel": return sentinel_value
        sentinel_value: Return value when on_guard_error="return_sentinel".
        max_history_steps: Maximum step records to retain in GuardState.
            Prevents unbounded memory growth (T3 mitigation). 10-1000.
        log_dir: Directory for JSONL log files. None = write to stdout.

    """

    # Typed as Any to avoid requiring langchain-core as a Pydantic dep.
    # At runtime the user passes a BaseChatModel (ChatOpenAI, etc.).
    # Pydantic cannot validate without importing langchain-core.
    escalation_model: Any = None
    # Name of the workhorse (cheap) model that is being guarded.
    # Populated by the user or the Guard class. Used in escalation
    # prompt and EscalationEvent for observability (#30, #31).
    workhorse_model_name: str = ""
    on_escalate: Literal["auto", "interrupt"] = "auto"
    escalation_prompt: str | None = None
    # default=1 is safest — most use cases (UC-1) need exactly one escalation
    # per guarded call.  le=10 prevents economic DoS (T6).
    # ge=1 ensures at least one escalation is allowed (otherwise guard is useless).
    max_escalations_per_run: int = Field(default=1, ge=1, le=10)
    # default_factory creates fresh TriggerConfig objects per Guard instance,
    # preventing shared mutable state between instances.  A plain default={}
    # dict would be created once at class definition time and shared by all
    # instances — a classic Python gotcha.
    triggers: dict[str, TriggerConfig] = Field(
        default_factory=lambda: {
            # All three trigger types enabled by default at max_retries=3.
            # Users can disable individual triggers by setting enabled=False
            # or adjust thresholds without removing the trigger entry.
            "repeated_error": TriggerConfig(max_retries=3),
            "test_failure": TriggerConfig(max_retries=3),
            "schema_invalid": TriggerConfig(max_retries=3),
        }
    )
    max_context_tokens: int = Field(default=4000, ge=500, le=32000)
    compress_context: bool = True
    # T2 mitigation: secret/PII redaction patterns
    redact_patterns: list[str] = Field(default_factory=list)
    redact_fields: list[str] = Field(default_factory=list)
    # T1 mitigation: prompt injection defense via delimiters
    sanitize_context: bool = True
    # OQ-3 resolution: configurable fail-open behaviour
    on_guard_error: Literal[
        "raise_original", "return_last_output", "return_sentinel"
    ] = "raise_original"
    sentinel_value: object | None = None
    # T3 mitigation: bounded history prevents memory exhaustion
    max_history_steps: int = Field(default=100, ge=10, le=1000)
    # None = stdout JSONL; path = file-based logging
    log_dir: str | None = None
