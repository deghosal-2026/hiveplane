"""Spend-velocity guard: pause a runaway burn before it exhausts the budget (M41-03).

Per-run/per-day budgets are ceilings; velocity is the derivative. A workload
within budget but spending at an anomalous rate is paused before it consumes the
rest.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from datetime import UTC, datetime, timedelta

from pydantic import BaseModel, ConfigDict, Field


class VelocityEvent(BaseModel):
    """One recorded spend event for a workload."""

    model_config = ConfigDict(extra="forbid")

    workload: str = Field(min_length=1)
    cost_usd: float = Field(ge=0.0)
    at: datetime


class VelocityBreach(BaseModel):
    """A spend-velocity breach with the observed rate and time-to-exhaustion."""

    model_config = ConfigDict(extra="forbid")

    workload: str = Field(min_length=1)
    spent_usd: float = Field(ge=0.0)
    limit_usd: float
    window_seconds: int = Field(gt=0)
    multiplier: float | None = None
    baseline_usd: float | None = None
    rate_per_minute: float = Field(ge=0.0)
    projected_exhaustion_seconds: int | None = Field(default=None, ge=0)
    rule_id: str = "guard.velocity"
    reason: str = "spend velocity exceeded"


class SpendVelocityGuard:
    """Tracks per-workload spend over a rolling window and flags anomalies."""

    def __init__(self, *, clock: Callable[[], datetime] | None = None) -> None:
        self._clock = clock or (lambda: datetime.now(UTC))
        self._events: dict[str, list[VelocityEvent]] = defaultdict(list)

    def record(self, workload: str, cost_usd: float) -> None:
        """Record a spend event for a workload."""
        self._events[workload].append(
            VelocityEvent(workload=workload, cost_usd=cost_usd, at=self._clock())
        )

    def spent(
        self, workload: str, *, window_seconds: int, at: datetime | None = None
    ) -> float:
        """Return the spend within the trailing window."""
        now = at or self._clock()
        cutoff = now - timedelta(seconds=window_seconds)
        return sum(
            event.cost_usd
            for event in self._events.get(workload, [])
            if event.at >= cutoff
        )

    def check(
        self,
        workload: str,
        *,
        window_seconds: int,
        limit_usd: float,
        at: datetime | None = None,
        multiplier: float | None = None,
        baseline_spent_usd: float | None = None,
        budget_remaining_usd: float | None = None,
    ) -> VelocityBreach | None:
        """Return a breach when spend exceeds the absolute or relative limit."""
        spent = self.spent(workload, window_seconds=window_seconds, at=at)
        absolute = spent > limit_usd
        relative = (
            multiplier is not None
            and baseline_spent_usd is not None
            and spent > multiplier * baseline_spent_usd
        )
        if not (absolute or relative):
            return None
        rate_per_minute = spent / (window_seconds / 60)
        exhaustion: int | None = None
        if budget_remaining_usd is not None and rate_per_minute > 0:
            exhaustion = int(budget_remaining_usd / rate_per_minute * 60)
        return VelocityBreach(
            workload=workload,
            spent_usd=spent,
            limit_usd=limit_usd,
            window_seconds=window_seconds,
            multiplier=multiplier,
            baseline_usd=baseline_spent_usd,
            rate_per_minute=rate_per_minute,
            projected_exhaustion_seconds=exhaustion,
        )
