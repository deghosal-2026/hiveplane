"""Budget spend and attribution storage."""

from __future__ import annotations

import threading
from typing import Protocol

from sqlalchemy import Engine, delete, select

from hiveplane.budget.models import CostAttribution
from hiveplane.config import Settings, get_settings
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import (
    BudgetDaySpendRow,
    BudgetRunSpendRow,
    BudgetTeamSpendRow,
    CostAttributionRow,
)


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


class PostgresBudgetStore:
    """A durable budget store backed by PostgreSQL (#128).

    Run, workload-day, and team-day totals live in dedicated aggregate tables so
    daily limits survive a control-plane restart; attributions live in
    ``cost_attributions``.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def add_run_spend(self, run_id: str, amount: float) -> None:
        """Add spend to a run total."""
        with self._session.begin() as session:
            row = session.get(BudgetRunSpendRow, run_id)
            if row is None:
                session.add(BudgetRunSpendRow(run_id=run_id, amount_usd=amount))
            else:
                row.amount_usd += amount

    def run_spend(self, run_id: str) -> float:
        """Return a run's total spend."""
        with self._session() as session:
            row = session.get(BudgetRunSpendRow, run_id)
            return row.amount_usd if row is not None else 0.0

    def add_day_spend(self, workload: str, day: str, amount: float) -> None:
        """Add spend to a workload's day total."""
        with self._session.begin() as session:
            row = session.get(BudgetDaySpendRow, (workload, day))
            if row is None:
                session.add(
                    BudgetDaySpendRow(workload=workload, day=day, amount_usd=amount)
                )
            else:
                row.amount_usd += amount

    def day_spend(self, workload: str, day: str) -> float:
        """Return a workload's day spend."""
        with self._session() as session:
            row = session.get(BudgetDaySpendRow, (workload, day))
            return row.amount_usd if row is not None else 0.0

    def add_team_spend(self, team: str, day: str, amount: float) -> None:
        """Add spend to a team's day total."""
        with self._session.begin() as session:
            row = session.get(BudgetTeamSpendRow, (team, day))
            if row is None:
                session.add(BudgetTeamSpendRow(team=team, day=day, amount_usd=amount))
            else:
                row.amount_usd += amount

    def team_spend(self, team: str, day: str) -> float:
        """Return a team's day spend."""
        with self._session() as session:
            row = session.get(BudgetTeamSpendRow, (team, day))
            return row.amount_usd if row is not None else 0.0

    def record_attribution(self, record: CostAttribution) -> None:
        """Append a cost attribution record."""
        with self._session.begin() as session:
            session.add(
                CostAttributionRow(
                    team=record.team,
                    workload=record.workload,
                    period=record.timestamp.date().isoformat(),
                    total_spend_usd=record.cost_usd,
                    payload=record.model_dump(mode="json"),
                )
            )

    def list_attributions(self, *, workload: str | None = None) -> list[CostAttribution]:
        """List attribution records, optionally filtered by workload."""
        statement = select(CostAttributionRow).order_by(CostAttributionRow.id)
        if workload is not None:
            statement = statement.where(CostAttributionRow.workload == workload)
        with self._session() as session:
            return [
                CostAttribution.model_validate(row.payload)
                for row in session.scalars(statement)
            ]

    def clear(self) -> None:
        """Delete all budget data; used by tests and destructive operations."""
        with self._session.begin() as session:
            for table in (
                BudgetRunSpendRow,
                BudgetDaySpendRow,
                BudgetTeamSpendRow,
                CostAttributionRow,
            ):
                session.execute(delete(table))


def build_budget_store(settings: Settings | None = None) -> BudgetStore:
    """Build the configured budget store (in-memory by default)."""
    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresBudgetStore(create_engine_from_settings(resolved))
    return InMemoryBudgetStore()
