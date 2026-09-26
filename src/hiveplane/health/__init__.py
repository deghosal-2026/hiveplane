"""Agent health model, SLO/error budget, and burn throttle (M42)."""

from __future__ import annotations

from hiveplane.health.models import (
    BurnRate,
    BurnThroughAction,
    HealthStatus,
    Objective,
    ObjectiveStatus,
    SloObjective,
    SloTarget,
    WorkloadHealth,
)
from hiveplane.health.service import HealthService, compute_health

__all__ = [
    "BurnRate",
    "BurnThroughAction",
    "HealthService",
    "HealthStatus",
    "Objective",
    "ObjectiveStatus",
    "SloObjective",
    "SloTarget",
    "WorkloadHealth",
    "compute_health",
]
