"""Budget burn metrics hooks (real exporters land with telemetry)."""

from __future__ import annotations

from typing import Protocol

from hiveplane.core.usage import BudgetLevel


class BudgetMetrics(Protocol):
    """Sink for budget burn and over-budget signals."""

    def record_spend(self, cost_usd: float, workload: str, team: str | None) -> None: ...

    def record_exceeded(self, level: BudgetLevel, workload: str) -> None: ...


class NullBudgetMetrics:
    """A metrics sink that records nothing."""

    def record_spend(self, cost_usd: float, workload: str, team: str | None) -> None:
        """Discard a spend signal."""

    def record_exceeded(self, level: BudgetLevel, workload: str) -> None:
        """Discard an over-budget signal."""
