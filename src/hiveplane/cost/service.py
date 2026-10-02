"""Cost service: attribution, budget periods, alerts, and showback (M49)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, date, datetime, timedelta
from uuid import uuid4

from hiveplane.cost.depth import BurnForecast, ChargebackRow, RoiReport, build_roi, forecast
from hiveplane.cost.models import (
    BudgetPeriod,
    BudgetScope,
    CarryRule,
    CostEvent,
    ShowbackReport,
    ShowbackRow,
    SpendCapExceededError,
    ThresholdAlert,
    UnattributedUsageError,
)
from hiveplane.cost.store import CostStore
from hiveplane.fleet.cost import CostPeriodKind
from hiveplane.tenancy.context import DEFAULT_CONTEXT, TenantContext, context_for_run


def new_alert_id() -> str:
    """Return a fresh opaque alert id."""
    return f"alert-{uuid4().hex[:20]}"


def period_start(kind: CostPeriodKind, when: datetime) -> date:
    """Return the inclusive start date of the period containing ``when``."""
    moment = when.date()
    if kind is CostPeriodKind.DAY:
        return moment
    if kind is CostPeriodKind.WEEK:
        return moment - timedelta(days=moment.weekday())
    return moment.replace(day=1)


def period_key(kind: CostPeriodKind, when: datetime) -> str:
    """Return a stable key for the period containing ``when``."""
    start = period_start(kind, when)
    if kind is CostPeriodKind.DAY:
        return start.isoformat()
    if kind is CostPeriodKind.WEEK:
        iso = start.isocalendar()
        return f"{iso.year}-W{iso.week:02d}"
    return f"{start.year}-{start.month:02d}"


def next_period_key(kind: CostPeriodKind, when: datetime) -> str:
    """Return the key of the period immediately after the one containing ``when``."""
    start = period_start(kind, when)
    if kind is CostPeriodKind.DAY:
        following = start + timedelta(days=1)
    elif kind is CostPeriodKind.WEEK:
        following = start + timedelta(days=7)
    else:
        following = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return period_key(
        kind, datetime(following.year, following.month, following.day, tzinfo=UTC)
    )


def period_bounds(kind: CostPeriodKind, when: datetime) -> tuple[datetime, datetime]:
    """Return the half-open ``[start, end)`` datetimes of the period containing ``when``."""
    start = period_start(kind, when)
    if kind is CostPeriodKind.DAY:
        following = start + timedelta(days=1)
    elif kind is CostPeriodKind.WEEK:
        following = start + timedelta(days=7)
    else:
        following = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
    return (
        datetime(start.year, start.month, start.day, tzinfo=UTC),
        datetime(following.year, following.month, following.day, tzinfo=UTC),
    )


class CostService:
    """Attributes usage, enforces budgets/caps, and serves showback."""

    def __init__(
        self,
        store: CostStore,
        *,
        thresholds: tuple[int, ...] = (50, 80, 100),
        clock: Callable[[], datetime] | None = None,
        alert_id_factory: Callable[[], str] = new_alert_id,
    ) -> None:
        self._store = store
        self._thresholds = thresholds
        self._clock = clock or (lambda: datetime.now(UTC))
        self._alert_id_factory = alert_id_factory

    # ------------------------------------------------------------------ #
    # Budgets
    # ------------------------------------------------------------------ #
    def set_budget(
        self,
        tenant_id: str,
        scope: BudgetScope,
        scope_id: str,
        kind: CostPeriodKind,
        limit_usd: float,
        *,
        carry_rule: CarryRule = CarryRule.NONE,
        cap_usd: float | None = None,
        enforced: bool = False,
        at: datetime | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> BudgetPeriod:
        """Create or update the budget bucket for the current period."""
        when = at or self._clock()
        key = period_key(kind, when)
        period_id = f"{tenant_id}:{scope.value}:{scope_id}:{kind.value}:{key}"
        period = BudgetPeriod(
            period_id=period_id,
            tenant_id=tenant_id,
            scope=scope,
            scope_id=scope_id,
            kind=kind,
            period_key=key,
            limit_usd=limit_usd,
            carry_rule=carry_rule,
            cap_usd=cap_usd,
            enforced=enforced,
        )
        existing = self._store.get_period(period_id, ctx=ctx)
        if existing is not None:
            period.spent_usd = existing.spent_usd
            period.carry_in_usd = existing.carry_in_usd
        self._store.save_period(period, ctx=ctx)
        return period

    def periods(
        self, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[BudgetPeriod]:
        """Return the tenant's budget periods."""
        return self._store.list_periods(tenant_id, ctx=ctx)

    def alerts(
        self, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[ThresholdAlert]:
        """Return the tenant's fired threshold alerts."""
        return self._store.list_alerts(tenant_id, ctx=ctx)

    # ------------------------------------------------------------------ #
    # Attribution & alerts
    # ------------------------------------------------------------------ #
    def record(
        self, event: CostEvent, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[ThresholdAlert]:
        """Record a usage event, applying budget spend and firing alerts."""
        missing = [
            field
            for field, value in (("tenant_id", event.tenant_id), ("workload_id", event.workload_id))
            if not value
        ]
        if missing:
            self._store.dead_letter(event, "missing attribution")
            raise UnattributedUsageError(event.event_id, missing)
        scope = ctx if ctx.scopes(event.tenant_id) else context_for_run(event.tenant_id)
        self._store.save_event(event, ctx=scope)
        fired: list[ThresholdAlert] = []
        for period in self._matching_periods(event, scope):
            period.spent_usd += event.cost_usd
            self._store.save_period(period, ctx=scope)
            fired.extend(self._evaluate(period, event.occurred_at, scope))
        return fired

    def mark_run_completed(
        self, run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> int:
        """Reflect a terminal run outcome onto its metering events (M49-05)."""
        return self._store.mark_run_completed(run_id, ctx=ctx)

    def _matching_periods(
        self, event: CostEvent, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[BudgetPeriod]:
        result: list[BudgetPeriod] = []
        for period in self._store.list_periods(event.tenant_id, ctx=ctx):
            if period.scope is BudgetScope.TEAM and period.scope_id != event.team_id:
                continue
            if period.scope is BudgetScope.WORKLOAD and period.scope_id != event.workload_id:
                continue
            if period.period_key != period_key(period.kind, event.occurred_at):
                continue
            result.append(period)
        return result

    def _evaluate(
        self,
        period: BudgetPeriod,
        when: datetime,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[ThresholdAlert]:
        fired: list[ThresholdAlert] = []
        if period.effective_limit_usd <= 0:
            return fired
        for threshold in self._thresholds:
            if period.spent_usd < period.effective_limit_usd * threshold / 100:
                continue
            dedup = f"{period.period_id}:{threshold}"
            if any(
                alert.alert_id == dedup
                for alert in self._store.list_alerts(period.tenant_id, ctx=ctx)
            ):
                continue
            alert = ThresholdAlert(
                alert_id=dedup,
                tenant_id=period.tenant_id,
                scope=period.scope,
                scope_id=period.scope_id,
                kind=period.kind,
                period_key=period.period_key,
                threshold=threshold,
                spent_usd=period.spent_usd,
                limit_usd=period.effective_limit_usd,
                fired_at=when,
            )
            self._store.save_alert(alert, ctx=ctx)
            fired.append(alert)
        return fired

    # ------------------------------------------------------------------ #
    # Caps & rollover
    # ------------------------------------------------------------------ #
    def check_cap(
        self,
        tenant_id: str,
        kind: CostPeriodKind,
        *,
        at: datetime | None = None,
        projected_usd: float = 0.0,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> None:
        """Deny (fail-closed) when a tenant cap would be exceeded."""
        when = at or self._clock()
        key = period_key(kind, when)
        periods = self._store.list_periods(tenant_id, ctx=ctx)
        for period in periods:
            if (
                period.scope is BudgetScope.TENANT
                and period.enforced
                and period.cap_usd is not None
                and period.period_key == key
                and period.spent_usd + projected_usd > period.cap_usd
            ):
                raise SpendCapExceededError(tenant_id, period.cap_usd, period.spent_usd)

    def rollover(
        self,
        tenant_id: str,
        kind: CostPeriodKind,
        *,
        at: datetime | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[BudgetPeriod]:
        """Materialize the next period, carrying over per each budget's rule."""
        when = at or self._clock()
        current_key = period_key(kind, when)
        next_key = next_period_key(kind, when)
        rolled: list[BudgetPeriod] = []
        for period in self._store.list_periods(tenant_id, ctx=ctx):
            if period.kind is not kind or period.period_key != current_key:
                continue
            if next_key == current_key:
                continue
            carry = 0.0
            if period.carry_rule is CarryRule.FULL:
                carry = period.remaining_usd
            elif period.carry_rule is CarryRule.CAPPED:
                carry = min(period.remaining_usd, period.limit_usd)
            next_id = f"{tenant_id}:{period.scope.value}:{period.scope_id}:{kind.value}:{next_key}"
            next_period = BudgetPeriod(
                period_id=next_id,
                tenant_id=tenant_id,
                scope=period.scope,
                scope_id=period.scope_id,
                kind=kind,
                period_key=next_key,
                limit_usd=period.limit_usd,
                carry_rule=period.carry_rule,
                carry_in_usd=carry,
                cap_usd=period.cap_usd,
                enforced=period.enforced,
            )
            existing = self._store.get_period(next_id, ctx=ctx)
            if existing is not None:
                # Idempotent materialization: never reset an already-populated
                # next period's spend or double-apply carry.
                next_period.spent_usd = existing.spent_usd
                next_period.carry_in_usd = existing.carry_in_usd
            self._store.save_period(next_period, ctx=ctx)
            rolled.append(next_period)
        return rolled

    # ------------------------------------------------------------------ #
    # Showback
    # ------------------------------------------------------------------ #
    def showback(
        self,
        tenant_id: str,
        kind: CostPeriodKind,
        *,
        at: datetime | None = None,
        group_by: str = "team",
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> ShowbackReport:
        """Compute cost-per-completed-task showback for one period."""
        when = at or self._clock()
        key = period_key(kind, when)
        start, end = period_bounds(kind, when)
        groups: dict[str, ShowbackRow] = {}
        total_cost = 0.0
        completed = 0
        savings = 0.0
        for event in self._store.list_events(tenant_id, ctx=ctx, start=start, end=end):
            group = event.team_id if group_by == "team" else event.workload_id
            row = groups.setdefault(
                group,
                ShowbackRow(
                    tenant_id=tenant_id,
                    team_id=event.team_id if group_by == "team" else None,
                    workload_id=event.workload_id if group_by != "team" else None,
                ),
            )
            row.total_cost_usd += event.cost_usd
            row.cache_savings_usd += event.saved_usd
            if event.retry or event.escalation:
                row.wasted_usd += event.cost_usd
            if event.completed:
                row.completed_tasks += 1
                completed += 1
            total_cost += event.cost_usd
            savings += event.saved_usd
        rows = sorted(groups.values(), key=lambda row: row.team_id or row.workload_id or "")
        for row in rows:
            if row.completed_tasks:
                row.cost_per_completed_task = row.total_cost_usd / row.completed_tasks
        return ShowbackReport(
            tenant_id=tenant_id,
            kind=kind,
            period_key=key,
            group_by=group_by,
            rows=rows,
            total_cost_usd=total_cost,
            total_completed_tasks=completed,
            fleet_cpct=(total_cost / completed) if completed else 0.0,
            cache_savings_usd=savings,
            unattributed=self._store.unattributed(),
        )

    # ------------------------------------------------------------------ #
    # Depth: forecast, chargeback, ROI (M50-06/07/08)
    # ------------------------------------------------------------------ #
    def forecast(
        self,
        tenant_id: str,
        kind: CostPeriodKind,
        *,
        at: datetime | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> BurnForecast:
        """Forecast period burn and overrun for the tenant-scope budget."""
        when = at or self._clock()
        key = period_key(kind, when)
        start = period_start(kind, when)
        limit: float | None = None
        spent = 0.0
        for period in self._store.list_periods(tenant_id, ctx=ctx):
            if (
                period.kind is kind
                and period.period_key == key
                and period.scope is BudgetScope.TENANT
            ):
                limit = period.effective_limit_usd
                spent = period.spent_usd
        elapsed = self._elapsed_fraction(kind, start, when)
        return forecast(spent_usd=spent, elapsed_fraction=elapsed, limit_usd=limit)

    def chargeback(
        self, tenant_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[ChargebackRow]:
        """Export per-attribution usage for billing (M50-06)."""
        totals: dict[str, tuple[float, int]] = {}
        for event in self._store.list_events(tenant_id, ctx=ctx):
            key = f"{event.tenant_id}/{event.team_id}/{event.workload_id}"
            cost, count = totals.get(key, (0.0, 0))
            totals[key] = (cost + event.cost_usd, count + 1)
        return [
            ChargebackRow(attribution_key=key, cost_usd=cost, events=count)
            for key, (cost, count) in sorted(totals.items())
        ]

    def roi(
        self,
        tenant_id: str,
        kind: CostPeriodKind,
        *,
        at: datetime | None = None,
        spend_threshold_usd: float = 10.0,
        roi_threshold: float = 1.0,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> RoiReport:
        """Build fleet ROI from spend and completed outcomes (M50-08)."""
        report = self.showback(tenant_id, kind, at=at, group_by="workload", ctx=ctx)
        outcomes = [
            {
                "workload_id": row.workload_id or "",
                "spend_usd": row.total_cost_usd,
                "value_usd": float(row.completed_tasks),
                "completed_tasks": row.completed_tasks,
            }
            for row in report.rows
        ]
        return build_roi(
            outcomes, spend_threshold_usd=spend_threshold_usd, roi_threshold=roi_threshold
        )

    @staticmethod
    def _elapsed_fraction(kind: CostPeriodKind, start: date, when: datetime) -> float:
        start_dt = datetime(start.year, start.month, start.day, tzinfo=UTC)
        if kind is CostPeriodKind.DAY:
            total = 24 * 3600.0
        elif kind is CostPeriodKind.WEEK:
            total = 7 * 24 * 3600.0
        else:
            next_month = (start.replace(day=28) + timedelta(days=4)).replace(day=1)
            total = (next_month - start).total_seconds()
        elapsed = (when - start_dt).total_seconds()
        return min(1.0, max(0.0, elapsed / total)) if total else 1.0
