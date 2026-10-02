"""Typed records for the agent health model and SLOs (M42)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class HealthStatus(StrEnum):
    """Composite health of a workload."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    UNHEALTHY = "unhealthy"
    INSUFFICIENT_DATA = "insufficient_data"


class Objective(StrEnum):
    """A service-level objective."""

    AVAILABILITY = "availability"
    QUALITY = "quality"


class ObjectiveStatus(StrEnum):
    """Computed status of an SLO objective."""

    OK = "ok"
    AT_RISK = "at_risk"
    BREACHED = "breached"


class SloTarget(BaseModel):
    """Per-workload SLO targets and window."""

    model_config = ConfigDict(extra="forbid")

    availability_target: float = Field(default=0.99, gt=0.0, le=1.0)
    quality_target: float | None = Field(default=None, gt=0.0, le=1.0)
    window_seconds: int = Field(default=86400, gt=0)


class SloObjective(BaseModel):
    """One objective's error-budget accounting."""

    model_config = ConfigDict(extra="forbid")

    objective: Objective
    target: float
    window_seconds: int
    error_budget: float = Field(ge=0.0)
    consumed: float = Field(ge=0.0)
    remaining: float = Field(ge=0.0)
    observed: float | None = None
    status: ObjectiveStatus


class WorkloadHealth(BaseModel):
    """The full health model for one workload over a window."""

    model_config = ConfigDict(extra="forbid")

    workload: str = Field(min_length=1)
    window_seconds: int = Field(gt=0)
    terminal_runs: int = Field(ge=0)
    failed_runs: int = Field(ge=0)
    failure_rate: float = Field(ge=0.0, le=1.0)
    mttr_seconds: int | None = Field(default=None, ge=0)
    readiness: bool
    quality_score: float | None = None
    drift_status: str = "clean"
    breaker_open: bool = False
    objectives: list[SloObjective] = Field(default_factory=list)
    insufficient_data: bool = False
    status: HealthStatus
    generated_at: AwareDatetime

    def objective(self, objective: Objective) -> SloObjective:
        """Return one objective's accounting, or raise when absent."""
        for entry in self.objectives:
            if entry.objective is objective:
                return entry
        raise KeyError(objective)


class BurnRate(BaseModel):
    """A burn-rate measurement for one objective/window (M42-04)."""

    model_config = ConfigDict(extra="forbid")

    workload: str = Field(min_length=1)
    objective: Objective = Objective.AVAILABILITY
    window_seconds: int = Field(gt=0)
    observed_error_rate: float = Field(ge=0.0, le=1.0)
    allowed_error_rate: float = Field(ge=0.0, le=1.0)
    burn_rate: float = Field(ge=0.0)
    critical: bool = False
    alert: bool = False
    rule_id: str = "health.burn"


class BurnThroughAction(BaseModel):
    """An automatic action taken when an error budget burns through (M42-05)."""

    model_config = ConfigDict(extra="forbid")

    workload: str = Field(min_length=1)
    action: str = Field(min_length=1)
    objective: Objective
    reason: str = Field(min_length=1)
    rule_id: str = "health.burn_through"
    burn_rate: float | None = None
