"""Storage for cost events, budget periods, alerts, and dead letters (M49)."""

from __future__ import annotations

from datetime import datetime
from typing import Any, Protocol, cast

from sqlalchemy import Engine, delete, select
from sqlalchemy.engine import CursorResult

from hiveplane.config import Settings, get_settings
from hiveplane.cost.models import BudgetPeriod, CostEvent, ThresholdAlert
from hiveplane.persistence.base import create_engine_from_settings, session_factory
from hiveplane.persistence.models import (
    BudgetAlertRow,
    BudgetPeriodRow,
    MeteringEventRow,
)
from hiveplane.tenancy.context import DEFAULT_CONTEXT, TenantContext


def _dead_letter_tenant(entry: dict[str, object]) -> str | None:
    """Extract the owning tenant from a dead-letter entry's embedded event."""
    event = entry.get("event")
    if isinstance(event, dict):
        tenant_id = event.get("tenant_id")
        if isinstance(tenant_id, str):
            return tenant_id
    return None


class CostStore(Protocol):
    """Storage interface for cost showback and budget periods."""

    def save_event(
        self, event: CostEvent, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def dead_letter(self, event: CostEvent, reason: str) -> None: ...

    def list_events(
        self,
        tenant_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[CostEvent]: ...

    def mark_run_completed(
        self, run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> int: ...

    def delete_events_before(
        self, *, cutoff: datetime, tenant_id: str, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> int: ...

    def unattributed(self) -> int: ...

    def dead_letters(self) -> list[dict[str, object]]: ...

    def save_period(
        self, period: BudgetPeriod, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def get_period(
        self, period_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> BudgetPeriod | None: ...

    def list_periods(
        self, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[BudgetPeriod]: ...

    def period_tenants(self) -> list[str]: ...

    def save_alert(
        self, alert: ThresholdAlert, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None: ...

    def list_alerts(
        self, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[ThresholdAlert]: ...

    def purge_tenant(self, tenant_id: str) -> int: ...

    def clear(self) -> None: ...


class InMemoryCostStore:
    """A process-local cost store."""

    def __init__(self) -> None:
        self._events: dict[str, CostEvent] = {}
        self._periods: dict[str, BudgetPeriod] = {}
        self._alerts: dict[str, ThresholdAlert] = {}
        self._dead: list[dict[str, object]] = []

    def save_event(
        self, event: CostEvent, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(event.tenant_id)
        self._events[event.event_id] = event.model_copy(deep=True)

    def dead_letter(self, event: CostEvent, reason: str) -> None:
        """Record an unattributable event; dead letters are a global operator view."""
        self._dead.append({"event": event.model_dump(mode="json"), "reason": reason})

    def list_events(
        self,
        tenant_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[CostEvent]:
        if not ctx.scopes(tenant_id):
            return []
        return [
            event.model_copy(deep=True)
            for _, event in sorted(self._events.items())
            if event.tenant_id == tenant_id
            and (start is None or event.occurred_at >= start)
            and (end is None or event.occurred_at < end)
        ]

    def mark_run_completed(
        self, run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> int:
        """Flip a run's metering events to completed; returns how many changed."""
        count = 0
        for event_id, event in list(self._events.items()):
            if (
                event.run_id == run_id
                and ctx.scopes(event.tenant_id)
                and not event.completed
            ):
                self._events[event_id] = event.model_copy(update={"completed": True})
                count += 1
        return count

    def delete_events_before(
        self, *, cutoff: datetime, tenant_id: str, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> int:
        if not ctx.scopes(tenant_id):
            return 0
        stale = [
            event_id
            for event_id, event in self._events.items()
            if event.tenant_id == tenant_id and event.occurred_at < cutoff
        ]
        for event_id in stale:
            del self._events[event_id]
        return len(stale)

    def unattributed(self) -> int:
        """Return the global dead-letter count (operator/system view)."""
        return len(self._dead)

    def dead_letters(self) -> list[dict[str, object]]:
        """Return the global dead-letter entries (operator/system view)."""
        return list(self._dead)

    def save_period(
        self, period: BudgetPeriod, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(period.tenant_id)
        self._periods[period.period_id] = period.model_copy(deep=True)

    def get_period(
        self, period_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> BudgetPeriod | None:
        period = self._periods.get(period_id)
        if period is None or not ctx.scopes(period.tenant_id):
            return None
        return period.model_copy(deep=True)

    def list_periods(
        self, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[BudgetPeriod]:
        if not ctx.scopes(tenant_id):
            return []
        return [
            period.model_copy(deep=True)
            for _, period in sorted(self._periods.items())
            if period.tenant_id == tenant_id
        ]

    def period_tenants(self) -> list[str]:
        """Return every tenant that has a budget period (rollover discovery)."""
        return sorted({period.tenant_id for period in self._periods.values()})

    def save_alert(
        self, alert: ThresholdAlert, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(alert.tenant_id)
        self._alerts[alert.alert_id] = alert.model_copy(deep=True)

    def list_alerts(
        self, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[ThresholdAlert]:
        if not ctx.scopes(tenant_id):
            return []
        return [
            alert.model_copy(deep=True)
            for _, alert in sorted(self._alerts.items())
            if alert.tenant_id == tenant_id
        ]

    def purge_tenant(self, tenant_id: str) -> int:
        event_ids = [
            event_id
            for event_id, event in self._events.items()
            if event.tenant_id == tenant_id
        ]
        for event_id in event_ids:
            del self._events[event_id]
        period_ids = [
            period_id
            for period_id, period in self._periods.items()
            if period.tenant_id == tenant_id
        ]
        for period_id in period_ids:
            del self._periods[period_id]
        alert_ids = [
            alert_id
            for alert_id, alert in self._alerts.items()
            if alert.tenant_id == tenant_id
        ]
        for alert_id in alert_ids:
            del self._alerts[alert_id]
        dead = [
            entry for entry in self._dead if _dead_letter_tenant(entry) == tenant_id
        ]
        self._dead = [
            entry for entry in self._dead if _dead_letter_tenant(entry) != tenant_id
        ]
        return len(event_ids) + len(period_ids) + len(alert_ids) + len(dead)

    def clear(self) -> None:
        self._events.clear()
        self._periods.clear()
        self._alerts.clear()
        self._dead.clear()


class PostgresCostStore:
    """A durable cost store backed by PostgreSQL (M49)."""

    def __init__(self, engine: Engine) -> None:
        self._engine = engine
        self._session = session_factory(engine)

    def save_event(
        self, event: CostEvent, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(event.tenant_id)
        with self._session.begin() as session:
            session.merge(
                MeteringEventRow(
                    event_id=event.event_id,
                    tenant_id=event.tenant_id,
                    team_id=event.team_id,
                    workload_id=event.workload_id,
                    run_id=event.run_id,
                    pipeline_run_id=event.pipeline_run_id,
                    cost_type=event.cost_type.value,
                    model=event.model,
                    cost_usd=event.cost_usd,
                    saved_usd=event.saved_usd,
                    occurred_at=event.occurred_at,
                    payload=event.model_dump(mode="json"),
                )
            )

    def dead_letter(self, event: CostEvent, reason: str) -> None:
        """Record an unattributable event; dead letters are a global operator view."""
        with self._session.begin() as session:
            session.merge(
                MeteringEventRow(
                    event_id=f"dead-{event.event_id}",
                    tenant_id="unattributed",
                    team_id=event.team_id or "unattributed",
                    workload_id=event.workload_id or "unattributed",
                    run_id=event.run_id,
                    pipeline_run_id=event.pipeline_run_id,
                    cost_type=event.cost_type.value,
                    model=event.model,
                    cost_usd=event.cost_usd,
                    saved_usd=event.saved_usd,
                    occurred_at=event.occurred_at,
                    payload={
                        "dead_letter": True,
                        "reason": reason,
                        **event.model_dump(mode="json"),
                    },
                )
            )

    def list_events(
        self,
        tenant_id: str,
        *,
        start: datetime | None = None,
        end: datetime | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[CostEvent]:
        if not ctx.scopes(tenant_id):
            return []
        statement = (
            select(MeteringEventRow)
            .where(MeteringEventRow.tenant_id == tenant_id)
            .order_by(MeteringEventRow.occurred_at)
        )
        if start is not None:
            statement = statement.where(MeteringEventRow.occurred_at >= start)
        if end is not None:
            statement = statement.where(MeteringEventRow.occurred_at < end)
        with self._session() as session:
            rows = session.scalars(statement).all()
        return [
            CostEvent.model_validate(row.payload)
            for row in rows
            if not row.payload.get("dead_letter")
        ]

    def mark_run_completed(
        self, run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> int:
        """Flip a run's metering events to completed; returns how many changed."""
        if not ctx.scopes(ctx.tenant_id):
            return 0
        count = 0
        with self._session.begin() as session:
            rows = session.scalars(
                select(MeteringEventRow).where(
                    MeteringEventRow.tenant_id == ctx.tenant_id,
                    MeteringEventRow.run_id == run_id,
                )
            ).all()
            for row in rows:
                payload = dict(row.payload)
                if payload.get("dead_letter") or payload.get("completed"):
                    continue
                payload["completed"] = True
                row.payload = payload
                count += 1
        return count

    def delete_events_before(
        self, *, cutoff: datetime, tenant_id: str, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> int:
        if not ctx.scopes(tenant_id):
            return 0
        with self._session.begin() as session:
            result = cast("CursorResult[Any]", session.execute(
                delete(MeteringEventRow).where(
                    MeteringEventRow.tenant_id == tenant_id,
                    MeteringEventRow.occurred_at < cutoff,
                )
            ))
            return int(result.rowcount or 0)

    def unattributed(self) -> int:
        """Return the global dead-letter count (operator/system view)."""
        with self._session() as session:
            rows = session.scalars(
                select(MeteringEventRow).where(MeteringEventRow.tenant_id == "unattributed")
            ).all()
        return len(rows)

    def dead_letters(self) -> list[dict[str, object]]:
        """Return the global dead-letter entries (operator/system view)."""
        with self._session() as session:
            rows = session.scalars(
                select(MeteringEventRow).where(MeteringEventRow.tenant_id == "unattributed")
            ).all()
        return [dict(row.payload) for row in rows]

    def save_period(
        self, period: BudgetPeriod, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(period.tenant_id)
        with self._session.begin() as session:
            session.merge(
                BudgetPeriodRow(
                    period_id=period.period_id,
                    tenant_id=period.tenant_id,
                    scope=period.scope.value,
                    scope_id=period.scope_id,
                    kind=period.kind.value,
                    period_key=period.period_key,
                    limit_usd=period.limit_usd,
                    spent_usd=period.spent_usd,
                    enforced=period.enforced,
                    payload=period.model_dump(mode="json"),
                )
            )

    def get_period(
        self, period_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> BudgetPeriod | None:
        with self._session() as session:
            row = session.get(BudgetPeriodRow, period_id)
            if row is None or not ctx.scopes(row.tenant_id):
                return None
            return BudgetPeriod.model_validate(row.payload)

    def list_periods(
        self, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[BudgetPeriod]:
        if not ctx.scopes(tenant_id):
            return []
        with self._session() as session:
            rows = session.scalars(
                select(BudgetPeriodRow)
                .where(BudgetPeriodRow.tenant_id == tenant_id)
                .order_by(BudgetPeriodRow.period_id)
            ).all()
        return [BudgetPeriod.model_validate(row.payload) for row in rows]

    def period_tenants(self) -> list[str]:
        """Return every tenant that has a budget period (rollover discovery)."""
        with self._session() as session:
            tenants = session.scalars(
                select(BudgetPeriodRow.tenant_id).distinct()
            ).all()
        return sorted(set(tenants))

    def save_alert(
        self, alert: ThresholdAlert, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> None:
        ctx.require(alert.tenant_id)
        with self._session.begin() as session:
            session.merge(
                BudgetAlertRow(
                    alert_id=alert.alert_id,
                    tenant_id=alert.tenant_id,
                    scope=alert.scope.value,
                    scope_id=alert.scope_id,
                    kind=alert.kind.value,
                    period_key=alert.period_key,
                    threshold=alert.threshold,
                    fired_at=alert.fired_at,
                    payload=alert.model_dump(mode="json"),
                )
            )

    def list_alerts(
        self, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[ThresholdAlert]:
        if not ctx.scopes(tenant_id):
            return []
        with self._session() as session:
            rows = session.scalars(
                select(BudgetAlertRow)
                .where(BudgetAlertRow.tenant_id == tenant_id)
                .order_by(BudgetAlertRow.fired_at)
            ).all()
        return [ThresholdAlert.model_validate(row.payload) for row in rows]

    def purge_tenant(self, tenant_id: str) -> int:
        with self._session.begin() as session:
            total = 0
            for table in (MeteringEventRow, BudgetPeriodRow, BudgetAlertRow):
                result = cast(
                    "CursorResult[Any]",
                    session.execute(
                        delete(table).where(table.tenant_id == tenant_id)
                    ),
                )
                total += int(result.rowcount or 0)
            dead = cast(
                "CursorResult[Any]",
                session.execute(
                    delete(MeteringEventRow).where(
                        MeteringEventRow.tenant_id == "unattributed",
                        MeteringEventRow.payload["tenant_id"].astext == tenant_id,
                    )
                ),
            )
            total += int(dead.rowcount or 0)
            return total

    def clear(self) -> None:
        with self._session.begin() as session:
            session.execute(delete(BudgetAlertRow))
            session.execute(delete(BudgetPeriodRow))
            session.execute(delete(MeteringEventRow))


def build_cost_store(settings: Settings | None = None) -> CostStore:
    """Build the configured cost store (in-memory by default)."""
    resolved = settings or get_settings()
    if resolved.execution.store == "postgres":
        return PostgresCostStore(create_engine_from_settings(resolved))
    return InMemoryCostStore()
