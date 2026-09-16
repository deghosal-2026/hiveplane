"""Cost pricing and per-run/per-day/per-team budget enforcement (DD-04)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane.budget.metrics import BudgetMetrics, NullBudgetMetrics
from hiveplane.budget.models import BudgetSnapshot, CostAttribution
from hiveplane.budget.pricing import CostTable
from hiveplane.budget.store import BudgetStore
from hiveplane.core.run import AdmissionContext
from hiveplane.core.usage import BudgetCheck, BudgetLevel, BudgetOutcome, UsageReport
from hiveplane.core.workload import AgentWorkload


class BudgetService:
    """Prices usage and enforces run, day, and team budget limits."""

    def __init__(
        self,
        store: BudgetStore,
        pricing: CostTable,
        *,
        metrics: BudgetMetrics | None = None,
        clock: Callable[[], datetime] | None = None,
    ) -> None:
        self._store = store
        self._pricing = pricing
        self._metrics = metrics or NullBudgetMetrics()
        self._clock = clock or (lambda: datetime.now(UTC))

    def _today(self) -> str:
        return self._clock().date().isoformat()

    def check(self, workload: AgentWorkload, context: AdmissionContext) -> BudgetCheck:
        """Return the headroom check for admitting a run."""
        budget = workload.spec.budget
        day = self._today()
        day_limit = budget.per_day_usd
        day_spent = self._store.day_spend(workload.name, day)
        if day_spent >= day_limit:
            return BudgetCheck(
                allowed=False,
                level=BudgetLevel.DAY,
                limit_usd=day_limit,
                spent_usd=day_spent,
                remaining_usd=0.0,
                reason=f"day budget exhausted for {workload.name!r}",
            )
        team_limit = budget.per_team_usd
        if team_limit is not None and workload.team is not None:
            team_spent = self._store.team_spend(workload.team, day)
            if team_spent >= team_limit:
                return BudgetCheck(
                    allowed=False,
                    level=BudgetLevel.TEAM,
                    limit_usd=team_limit,
                    spent_usd=team_spent,
                    remaining_usd=0.0,
                    reason=f"team budget exhausted for {workload.team!r}",
                )
        return BudgetCheck(
            allowed=True,
            level=BudgetLevel.RUN,
            limit_usd=budget.per_run_usd,
            spent_usd=0.0,
            remaining_usd=budget.per_run_usd,
        )

    def record_usage(self, workload: AgentWorkload, report: UsageReport) -> BudgetOutcome:
        """Price a usage event, accumulate spend, and return the run check."""
        if report.model_identity is not None:
            cost = self._pricing.price(
                report.model_identity, report.input_tokens, report.output_tokens
            )
        else:
            cost = report.cost_usd
        day = self._today()
        self._store.add_run_spend(report.run_id, cost)
        self._store.add_day_spend(workload.name, day, cost)
        if workload.team is not None:
            self._store.add_team_spend(workload.team, day, cost)
        self._store.record_attribution(
            CostAttribution(
                run_id=report.run_id,
                workload=workload.name,
                team=workload.team,
                model_identity=report.model_identity,
                input_tokens=report.input_tokens,
                output_tokens=report.output_tokens,
                tool_calls=report.tool_calls,
                cost_usd=cost,
                timestamp=report.timestamp,
            )
        )
        self._metrics.record_spend(cost, workload.name, workload.team)

        budget = workload.spec.budget
        run_spent = self._store.run_spend(report.run_id)
        if run_spent > budget.per_run_usd:
            self._metrics.record_exceeded(BudgetLevel.RUN, workload.name)
            return BudgetOutcome(
                check=self._exceeded(BudgetLevel.RUN, budget.per_run_usd), cost_usd=cost
            )
        day_spent = self._store.day_spend(workload.name, day)
        if day_spent > budget.per_day_usd:
            self._metrics.record_exceeded(BudgetLevel.DAY, workload.name)
            return BudgetOutcome(
                check=self._exceeded(BudgetLevel.DAY, budget.per_day_usd), cost_usd=cost
            )
        team_limit = budget.per_team_usd
        if team_limit is not None and workload.team is not None:
            team_spent = self._store.team_spend(workload.team, day)
            if team_spent > team_limit:
                self._metrics.record_exceeded(BudgetLevel.TEAM, workload.name)
                return BudgetOutcome(
                    check=self._exceeded(BudgetLevel.TEAM, team_limit), cost_usd=cost
                )
        return BudgetOutcome(
            check=BudgetCheck(
                allowed=True,
                level=BudgetLevel.RUN,
                limit_usd=budget.per_run_usd,
                spent_usd=run_spent,
                remaining_usd=max(0.0, budget.per_run_usd - run_spent),
            ),
            cost_usd=cost,
        )

    def snapshot(self, workload: AgentWorkload, run_id: str) -> BudgetSnapshot:
        """Return current spend for a run, workload day, and team day."""
        day = self._today()
        team = workload.team
        return BudgetSnapshot(
            workload=workload.name,
            team=team,
            day=day,
            run_usd=self._store.run_spend(run_id),
            day_usd=self._store.day_spend(workload.name, day),
            team_usd=self._store.team_spend(team, day) if team is not None else 0.0,
        )

    @staticmethod
    def _exceeded(level: BudgetLevel, limit: float) -> BudgetCheck:
        return BudgetCheck(
            allowed=False,
            level=level,
            limit_usd=limit,
            spent_usd=limit,
            remaining_usd=0.0,
            reason=f"{level.value} budget exceeded",
        )
