"""Redaction flag handling."""

from __future__ import annotations

from cauterule.models.trajectory import Trajectory


def mark_redacted(
    trajectory: Trajectory,
    extra_patterns: list[str] | tuple[str, ...] | None = None,
) -> Trajectory:
    """Return *trajectory* scrubbed with ``redacted=True`` (#500).

    Previously this only set the flag and copied content verbatim, so
    callers believing the output was safe to log were wrong. It now
    delegates to :func:`cauterule.redaction.engine.redact_trajectory`.

    If already redacted, returns the same trajectory without modification.
    """
    if trajectory.redacted:
        return trajectory
    from cauterule.redaction.engine import redact_trajectory

    return redact_trajectory(trajectory, extra_patterns)


def is_redacted(trajectory: Trajectory) -> bool:
    """Return ``True`` if *trajectory* is flagged as redacted."""
    return trajectory.redacted
