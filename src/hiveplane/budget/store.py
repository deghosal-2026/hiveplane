"""Budget spend and attribution storage.

Spend totals are keyed by tenant: run, workload-day, and team-day aggregates
all include the acting context's ``tenant_id``, so two tenants' budgets are
independent. Attributions carry their own ``tenant_id`` and a write that
crosses a tenant boundary raises.
"""

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
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext


class BudgetStore(Protocol):
    """Storage for spend totals and attributions."""

    def add_run_spend(
        self, run_id: str, amount: float, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def run_spend(
        self, run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> float: ...

    def add_day_spend(
        self,
        workload: str,
        day: str,
        amount: float,
        *,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> None: ...

    def day_spend(
        self, workload: str, day: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> float: ...

    def add_team_spend(
        self,
        team: str,
        day: str,
        amount: float,
        *,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> None: ...

    def team_spend(
        self, team: str, day: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> float: ...

    def record_attribution(
        self, record: CostAttribution, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def list_attributions(
        self,
        *,
        workload: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[CostAttribution]: ...


class InMemoryBudgetStore:
    """A process-local, thread-safe budget store with tenant-keyed totals."""

    def __init__(self) -> None:
        self._lock = threading.RLock()
        self._run_spend: dict[tuple[str, str], float] = {}
        self._day_spend: dict[tuple[str, str, str], float] = {}
        self._team_spend: dict[tuple[str, str, str], float] = {}
        self._attributions: list[CostAttribution] = []

    def add_run_spend(
        self, run_id: str, amount: float, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        """Add spend to a run total."""
        with self._lock:
            key = (ctx.tenant_id, run_id)
            self._run_spend[key] = self._run_spend.get(key, 0.0) + amount

    def run_spend(self, run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT) -> float:
        """Return a run's total spend."""
        with self._lock:
            return self._run_spend.get((ctx.tenant_id, run_id), 0.0)

    def add_day_spend(
        self,
        workload: str,
        day: str,
        amount: float,
        *,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> None:
        """Add spend to a workload's day total."""
        with self._lock:
            key = (ctx.tenant_id, workload, day)
            self._day_spend[key] = self._day_spend.get(key, 0.0) + amount

    def day_spend(
        self, workload: str, day: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> float:
        """Return a workload's day spend."""
        with self._lock:
            return self._day_spend.get((ctx.tenant_id, workload, day), 0.0)

    def add_team_spend(
        self,
        team: str,
        day: str,
        amount: float,
        *,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> None:
        """Add spend to a team's day total."""
        with self._lock:
            key = (ctx.tenant_id, team, day)
            self._team_spend[key] = self._team_spend.get(key, 0.0) + amount

    def team_spend(self, team: str, day: str, *, ctx: TenantContext = DEFAULT_CONTEXT) -> float:
        """Return a team's day spend."""
        with self._lock:
            return self._team_spend.get((ctx.tenant_id, team, day), 0.0)

    def record_attribution(
        self, record: CostAttribution, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        """Append a cost attribution record."""
        ctx.require(record.tenant_id)
        with self._lock:
            self._attributions.append(record.model_copy(deep=True))

    def list_attributions(
        self,
        *,
        workload: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[CostAttribution]:
        """List attribution records, optionally filtered by workload."""
        with self._lock:
            records = [
                record
                for record in self._attributions
                if ctx.scopes(record.tenant_id)
                and (workload is None or record.workload == workload)
            ]
            return [record.model_copy(deep=True) for record in records]


class PostgresBudgetStore:
    """A durable budget store backed by PostgreSQL (#128).

    Run, workload-day, and team-day totals live in dedicated aggregate tables so
    daily limits survive a control-plane restart; attributions live in
    ``cost_attributions``. Aggregate primary keys are tenant-qualified.
    """

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def add_run_spend(
        self, run_id: str, amount: float, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        """Add spend to a run total."""
        with self._session.begin() as session:
            row = session.get(BudgetRunSpendRow, (ctx.tenant_id, run_id))
            if row is None:
                session.add(
                    BudgetRunSpendRow(
                        tenant_id=ctx.tenant_id, run_id=run_id, amount_usd=amount
                    )
                )
            else:
                row.amount_usd += amount

    def run_spend(self, run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT) -> float:
        """Return a run's total spend."""
        with self._session() as session:
            row = session.get(BudgetRunSpendRow, (ctx.tenant_id, run_id))
            return row.amount_usd if row is not None else 0.0

    def add_day_spend(
        self,
        workload: str,
        day: str,
        amount: float,
        *,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> None:
        """Add spend to a workload's day total."""
        with self._session.begin() as session:
            row = session.get(BudgetDaySpendRow, (ctx.tenant_id, workload, day))
            if row is None:
                session.add(
                    BudgetDaySpendRow(
                        tenant_id=ctx.tenant_id,
                        workload=workload,
                        day=day,
                        amount_usd=amount,
                    )
                )
            else:
                row.amount_usd += amount

    def day_spend(
        self, workload: str, day: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> float:
        """Return a workload's day spend."""
        with self._session() as session:
            row = session.get(BudgetDaySpendRow, (ctx.tenant_id, workload, day))
            return row.amount_usd if row is not None else 0.0

    def add_team_spend(
        self,
        team: str,
        day: str,
        amount: float,
        *,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> None:
        """Add spend to a team's day total."""
        with self._session.begin() as session:
            row = session.get(BudgetTeamSpendRow, (ctx.tenant_id, team, day))
            if row is None:
                session.add(
                    BudgetTeamSpendRow(
                        tenant_id=ctx.tenant_id, team=team, day=day, amount_usd=amount
                    )
                )
            else:
                row.amount_usd += amount

    def team_spend(self, team: str, day: str, *, ctx: TenantContext = DEFAULT_CONTEXT) -> float:
        """Return a team's day spend."""
        with self._session() as session:
            row = session.get(BudgetTeamSpendRow, (ctx.tenant_id, team, day))
            return row.amount_usd if row is not None else 0.0

    def record_attribution(
        self, record: CostAttribution, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        """Append a cost attribution record."""
        ctx.require(record.tenant_id)
        with self._session.begin() as session:
            session.add(
                CostAttributionRow(
                    tenant_id=record.tenant_id,
                    team=record.team,
                    team_id=record.team,
                    workload=record.workload,
                    period=record.timestamp.date().isoformat(),
                    total_spend_usd=record.cost_usd,
                    attribution_key=record.attribution_key,
                    payload=record.model_dump(mode="json"),
                )
            )

    def list_attributions(
        self,
        *,
        workload: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[CostAttribution]:
        """List attribution records, optionally filtered by workload."""
        statement = select(CostAttributionRow).order_by(CostAttributionRow.id)
        if not ctx.is_system:
            statement = statement.where(CostAttributionRow.tenant_id == ctx.tenant_id)
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
