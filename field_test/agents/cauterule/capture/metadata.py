"""Trajectory metadata enrichment."""

from __future__ import annotations

import platform
from typing import Any

from cauterule.capture.taxonomy import classify_taxonomy
from cauterule.models.trajectory import AgentConfig, Environment, Trajectory


def enrich_trajectory(
    trajectory: Trajectory,
    *,
    domain: str | None = None,
    severity: str | None = None,
    tags: tuple[str, ...] | None = None,
    quality_label: str | None = None,
) -> Trajectory:
    """Return a new :class:`Trajectory` with enriched metadata.

    If a field is already set on *trajectory*, it is preserved unless an
    explicit override is provided via kwargs. Otherwise defaults are inferred.

    Args:
        trajectory: Base trajectory.
        domain: Override domain.
        severity: Override severity (low|medium|high).
        tags: Override tags.
        quality_label: Override quality_label.
    """
    # Infer domain from taxonomy or failure_class.
    inferred_domain: str | None = trajectory.domain
    if inferred_domain is None and trajectory.failure_class:
        inferred_domain = trajectory.failure_class.split("/")[0]
    elif inferred_domain is None and trajectory.steps:
        # Fallback to taxonomy from first error step.
        first = trajectory.steps[0]
        inferred_domain = classify_taxonomy(first.tool, first.error, first.output).split("/")[0]

    # Infer severity: high if failure, medium if steps>1, low otherwise.
    inferred_severity: str | None = trajectory.severity
    if inferred_severity is None:
        if not trajectory.success:
            inferred_severity = "medium"
        else:
            inferred_severity = "low"

    # Infer tags from domain and failure_class.
    inferred_tags: tuple[str, ...] = trajectory.tags
    if not inferred_tags and trajectory.failure_class:
        inferred_tags = tuple(p for p in trajectory.failure_class.split("/") if p)

    # Infer quality_label: default clear for failures.
    inferred_quality: str | None = trajectory.quality_label
    if inferred_quality is None:
        inferred_quality = "clear" if not trajectory.success else None

    # Environment: fill OS if missing.
    inferred_env = trajectory.environment
    if inferred_env is None:
        inferred_env = Environment(os=platform.system().lower(), ci=False)
    elif inferred_env.os is None:
        inferred_env = Environment(os=platform.system().lower(), ci=inferred_env.ci)

    # Apply explicit overrides.
    final_domain = domain if domain is not None else inferred_domain
    final_severity = severity if severity is not None else inferred_severity
    final_tags = tags if tags is not None else inferred_tags
    final_quality = quality_label if quality_label is not None else inferred_quality

    # Rebuild trajectory with enriched fields (frozen dataclass, so create new).
    return Trajectory(
        id=trajectory.id,
        timestamp=trajectory.timestamp,
        task=trajectory.task,
        steps=trajectory.steps,
        success=trajectory.success,
        failure_point=trajectory.failure_point,
        failure_class=trajectory.failure_class,
        quality_label=final_quality,  # type: ignore[arg-type]
        domain=final_domain,
        severity=final_severity,  # type: ignore[arg-type]
        tags=final_tags,
        agent_config=trajectory.agent_config or AgentConfig(),
        environment=inferred_env,
        redacted=trajectory.redacted,
        # #770: preserve adapter-set trust/annotation fields through the rebuild.
        injection_signal=trajectory.injection_signal,
        expected_outcome=trajectory.expected_outcome,
        expected_outcome_rationale=trajectory.expected_outcome_rationale,
        expected_outcome_confidence=trajectory.expected_outcome_confidence,
    )


def enrich_from_dict(data: dict[str, Any]) -> Trajectory:
    """Enrich a trajectory dict and return a :class:`Trajectory`.

    Convenience for JSONL ingestion.
    """
    traj = Trajectory.from_dict(data)
    return enrich_trajectory(traj)
