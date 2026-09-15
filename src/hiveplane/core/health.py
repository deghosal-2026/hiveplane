"""Agent health and SLO models (PRD 05: observability & health)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field

from hiveplane.core.types import Duration


class ProbeType(StrEnum):
    """Readiness probe strategy."""

    DRY_RUN = "dry_run"


class ReadinessProbe(BaseModel):
    """How the control plane checks agent readiness."""

    model_config = ConfigDict(extra="forbid")

    type: ProbeType = ProbeType.DRY_RUN
    interval: Duration = 60


class Slo(BaseModel):
    """Service-level objectives for an agent workload."""

    model_config = ConfigDict(extra="forbid")

    availability_target: float = Field(default=0.99, gt=0.0, le=1.0)
    quality_target: float = Field(default=0.90, gt=0.0, le=1.0)
    error_budget_window: Duration = 86400


class HealthSpec(BaseModel):
    """Health configuration for a workload."""

    model_config = ConfigDict(extra="forbid")

    readiness_probe: ReadinessProbe = Field(default_factory=ReadinessProbe)
    slo: Slo = Field(default_factory=Slo)
    failure_rate_threshold: float = Field(default=0.15, ge=0.0, le=1.0)
