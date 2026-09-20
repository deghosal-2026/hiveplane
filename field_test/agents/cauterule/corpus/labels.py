"""Trajectory quality-label assignment."""

from __future__ import annotations

from cauterule.models.trajectory import QualityLabel, Trajectory


def label_trajectory(trajectory: Trajectory, label: QualityLabel) -> Trajectory:
    """Return a new :class:`Trajectory` with the given *label* set on *trajectory*.

    Args:
        trajectory: Original trajectory (immutable, unchanged).
        label: Quality label to assign.

    Returns:
        New trajectory with updated quality_label.
    """
    return Trajectory(
        id=trajectory.id,
        timestamp=trajectory.timestamp,
        task=trajectory.task,
        steps=trajectory.steps,
        success=trajectory.success,
        failure_point=trajectory.failure_point,
        failure_class=trajectory.failure_class,
        quality_label=label,
        domain=trajectory.domain,
        severity=trajectory.severity,
        tags=trajectory.tags,
        agent_config=trajectory.agent_config,
        environment=trajectory.environment,
        redacted=trajectory.redacted,
    )
