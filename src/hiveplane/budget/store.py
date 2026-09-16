"""Budget spend and attribution storage."""

from __future__ import annotations

import threading
from typing import Protocol

from hiveplane.budget.models import CostAttribution


class BudgetStore(Protocol):
    """Storage for spend totals and attributions."""

    def add_run_spend(self, run_id: str, amount: float) -> None: ...

    def run_spend(self, run_id: str) -> float: ...

    def add_day_spend(self, workload: str, day: str, amount: float) -> None: ...

    def day_spend(self, workload: str, day: str) -> float: ...

    def add_team_spend(self, team: str, day: str, amount: float) -> None: ...

    def team_spend(self, team: str, day: str) -> float: ...

    def record_attribution(self, record: CostAttribution) -> None: ...

    def list_attributions(self, *, workload: str | None = None) -> list[CostAttribution]: ...


class InMemoryBudgetStore:
    """A process-local, thread-safe budget store."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._run_spend: dict[str, float] = {}
        self._day_spend: dict[tuple[str, str], float] = {}
        self._team_spend: dict[tuple[str, str], float] = {}
        self._attributions: list[CostAttribution] = []

    def add_run_spend(self, run_id: str, amount: float) -> None:
        """Add spend to a run total."""
        with self._lock:
            self._run_spend[run_id] = self._run_spend.get(run_id, 0.0) + amount

    def run_spend(self, run_id: str) -> float:
        """Return a run's total spend."""
        with self._lock:
            return self._run_spend.get(run_id, 0.0)

    def add_day_spend(self, workload: str, day: str, amount: float) -> None:
        """Add spend to a workload's day total."""
        with self._lock:
            key = (workload, day)
            self._day_spend[key] = self._day_spend.get(key, 0.0) + amount

    def day_spend(self, workload: str, day: str) -> float:
        """Return a workload's day spend."""
        with self._lock:
            return self._day_spend.get((workload, day), 0.0)

    def add_team_spend(self, team: str, day: str, amount: float) -> None:
        """Add spend to a team's day total."""
        with self._lock:
            key = (team, day)
            self._team_spend[key] = self._team_spend.get(key, 0.0) + amount

    def team_spend(self, team: str, day: str) -> float:
        """Return a team's day spend."""
        with self._lock:
            return self._team_spend.get((team, day), 0.0)

    def record_attribution(self, record: CostAttribution) -> None:
        """Append a cost attribution record."""
        with self._lock:
            self._attributions.append(record.model_copy(deep=True))

    def list_attributions(self, *, workload: str | None = None) -> list[CostAttribution]:
        """List attribution records, optionally filtered by workload."""
        with self._lock:
            records = list(self._attributions)
        if workload is not None:
            records = [record for record in records if record.workload == workload]
        return [record.model_copy(deep=True) for record in records]
