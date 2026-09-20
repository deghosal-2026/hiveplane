"""Redaction engine."""

from __future__ import annotations

import re
from typing import Any

from cauterule.models.trajectory import AgentConfig, Environment, Step, Trajectory
from cauterule.redaction.patterns import get_builtin_patterns

_REDACTED = "[REDACTED]"


def _compile_extra(patterns: list[str] | tuple[str, ...] | None) -> list[re.Pattern[str]]:
    if not patterns:
        return []
    compiled: list[re.Pattern[str]] = []
    for p in patterns:
        try:
            compiled.append(re.compile(p))
        except re.error:
            # Invalid regex: treat as literal substring.
            compiled.append(re.compile(re.escape(p)))
    return compiled


def redact_text(text: str, extra_patterns: list[str] | tuple[str, ...] | None = None) -> str:
    """Redact secrets in *text* using built-in and *extra_patterns*.

    Args:
        text: Input text.
        extra_patterns: Additional regex patterns for custom secrets.
    """
    if not text:
        return text
    redacted = text
    for pat in get_builtin_patterns():
        redacted = pat.sub(_REDACTED, redacted)
    for pat in _compile_extra(extra_patterns):
        redacted = pat.sub(_REDACTED, redacted)
    return redacted


def _redact_value(value: Any, extra_patterns: list[str] | tuple[str, ...] | None) -> Any:
    if isinstance(value, str):
        return redact_text(value, extra_patterns)
    if isinstance(value, dict):
        # Redact keys as well as values (#502) — secrets hide in key names too.
        return {
            _redact_value(k, extra_patterns): _redact_value(v, extra_patterns)
            for k, v in value.items()
        }
    if isinstance(value, (list, tuple)):
        redacted_list = [_redact_value(v, extra_patterns) for v in value]
        return type(value)(redacted_list) if isinstance(value, tuple) else redacted_list
    if isinstance(value, (set, frozenset)):
        # Recurse and rebuild the same type; only str leaves are transformed,
        # so other scalars (bool/int/None) pass through untouched (#502).
        redacted_set = [_redact_value(v, extra_patterns) for v in value]
        return type(value)(redacted_set)
    return value


def redact_trajectory(
    trajectory: Trajectory,
    extra_patterns: list[str] | tuple[str, ...] | None = None,
) -> Trajectory:
    """Return a new :class:`Trajectory` with secrets redacted.

    Redacts ``task``, ``failure_class``, and all step ``input``/``output``/``error``/``state``.
    Sets ``redacted`` to ``True``.
    """
    # Check if already redacted: no-op to avoid double processing, but still ensure flag.
    # We still redact even if already redacted (idempotent).

    redacted_steps: list[Step] = []
    for step in trajectory.steps:
        redacted_state = None
        if step.state is not None:
            redacted_state = _redact_value(step.state, extra_patterns)
        redacted_steps.append(
            Step(
                step_number=step.step_number,
                tool=step.tool,  # tool name not redacted (not a secret)
                input=redact_text(step.input, extra_patterns) if step.input is not None else None,
                output=redact_text(step.output, extra_patterns)
                if step.output is not None
                else None,
                error=redact_text(step.error, extra_patterns) if step.error is not None else None,
                state=redacted_state,
            )
        )

    # Redact top-level fields.
    redacted_task = redact_text(trajectory.task, extra_patterns)
    redacted_failure_class = (
        redact_text(trajectory.failure_class, extra_patterns) if trajectory.failure_class else None
    )
    # Formerly-skipped free-text / secret-capable fields (#502).
    redacted_domain = (
        redact_text(trajectory.domain, extra_patterns) if trajectory.domain is not None else None
    )
    redacted_quality_label = trajectory.quality_label
    if redacted_quality_label is not None and contains_secret(
        redacted_quality_label, extra_patterns
    ):
        # Secret-shaped label: scrub it; falls back to None rather than
        # storing an invalid enum value.
        redacted_quality_label = None
    redacted_tags = tuple(redact_text(t, extra_patterns) for t in trajectory.tags)
    redacted_agent_config = None
    if trajectory.agent_config is not None:
        redacted_agent_config = AgentConfig(
            model=redact_text(trajectory.agent_config.model, extra_patterns)
            if trajectory.agent_config.model is not None
            else None,
            tools=tuple(redact_text(t, extra_patterns) for t in trajectory.agent_config.tools),
        )
    redacted_environment = None
    if trajectory.environment is not None:
        redacted_environment = Environment(
            os=redact_text(trajectory.environment.os, extra_patterns)
            if trajectory.environment.os is not None
            else None,
            ci=trajectory.environment.ci,
        )

    return Trajectory(
        id=trajectory.id,
        timestamp=trajectory.timestamp,
        task=redacted_task,
        steps=tuple(redacted_steps),
        success=trajectory.success,
        failure_point=trajectory.failure_point,
        failure_class=redacted_failure_class,
        quality_label=redacted_quality_label,
        domain=redacted_domain,
        severity=trajectory.severity,
        tags=redacted_tags,
        agent_config=redacted_agent_config,
        environment=redacted_environment,
        redacted=True,
    )


def contains_secret(text: str, extra_patterns: list[str] | tuple[str, ...] | None = None) -> bool:
    """Return ``True`` if *text* contains a secret pattern."""
    if not text:
        return False
    for pat in get_builtin_patterns():
        if pat.search(text):
            return True
    for pat in _compile_extra(extra_patterns):
        if pat.search(text):
            return True
    return False
