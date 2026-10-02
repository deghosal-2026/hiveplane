"""Cost pricing and per-run/per-day/per-team budget enforcement (DD-04)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from hiveplane import metrics
from hiveplane.budget.errors import MissingModelIdentityError
from hiveplane.budget.models import BudgetSnapshot, CostAttribution
from hiveplane.budget.pricing import CostTable
from hiveplane.budget.store import BudgetStore
from hiveplane.core.run import AdmissionContext
from hiveplane.core.usage import BudgetCheck, BudgetLevel, BudgetOutcome, UsageReport
from hiveplane.core.workload import AgentWorkload
from hiveplane.cost.depth import CostEstimator
from hiveplane.cost.models import CostEvent, SpendCapExceededError
from hiveplane.cost.service import CostService
from hiveplane.fleet.cost import CostPeriodKind, CostType
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext
from hiveplane.tenancy.context import DEFAULT_TEAM_ID


class BudgetService:
    """Prices usage and enforces run, day, and team budget limits."""

    def __init__(
        self,
        store: BudgetStore,
        pricing: CostTable,
        *,
        clock: Callable[[], datetime] | None = None,
        cost_service: CostService | None = None,
    ) -> None:
        self._store = store
        self._pricing = pricing
        self._clock = clock or (lambda: datetime.now(UTC))
        self._cost_service = cost_service
        self._estimator: CostEstimator | None = None

    def attach_estimator(self, estimator: CostEstimator | None) -> None:
        """Bind the pre-admission cost estimator used to project tenant caps."""
        self._estimator = estimator

    def _today(self) -> str:
        return self._clock().date().isoformat()

    def check(
        self,
        workload: AgentWorkload,
        context: AdmissionContext,
        *,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> BudgetCheck:
        """Return the headroom check for admitting a run."""
        budget = workload.spec.budget
        day = self._today()
        day_limit = budget.per_day_usd
        day_spent = self._store.day_spend(workload.name, day, ctx=ctx)
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
            team_spent = self._store.team_spend(workload.team, day, ctx=ctx)
            if team_spent >= team_limit:
                return BudgetCheck(
                    allowed=False,
                    level=BudgetLevel.TEAM,
                    limit_usd=team_limit,
                    spent_usd=team_spent,
                    remaining_usd=0.0,
                    reason=f"team budget exhausted for {workload.team!r}",
                )
        cap = self._tenant_cap(ctx, projected_usd=self._projected_usd(workload))
        if cap is not None:
            return cap
        return BudgetCheck(
            allowed=True,
            level=BudgetLevel.RUN,
            limit_usd=budget.per_run_usd,
            spent_usd=0.0,
            remaining_usd=budget.per_run_usd,
        )

    def _projected_usd(self, workload: AgentWorkload) -> float:
        """Return the p90 pre-admission estimate for a workload, if known."""
        if self._estimator is None:
            return 0.0
        return self._estimator.estimate(workload.name, "default").p90_usd

    def _tenant_cap(
        self, ctx: TenantContext, *, projected_usd: float = 0.0
    ) -> BudgetCheck | None:
        """Return a fail-closed denial when any enforced tenant cap is hit.

        Every configured period kind (day/week/month) is evaluated, so a cap set
        on a non-DAY period still hard-stops admission and usage. ``projected_usd``
        is the pre-admission p90 estimate, so a cap can stop a run before it
        starts rather than only after spend lands.
        """
        if self._cost_service is None:
            return None
        for kind in CostPeriodKind:
            try:
                self._cost_service.check_cap(
                    ctx.tenant_id,
                    kind,
                    at=self._clock(),
                    projected_usd=projected_usd,
                    ctx=ctx,
                )
            except SpendCapExceededError as exc:
                return BudgetCheck(
                    allowed=False,
                    level=BudgetLevel.DAY,
                    limit_usd=exc.cap_usd,
                    spent_usd=exc.spent_usd,
                    remaining_usd=0.0,
                    reason="tenant spend cap exceeded",
                )
        return None

    def record_usage(
        self,
        workload: AgentWorkload,
        report: UsageReport,
        *,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> BudgetOutcome:
        """Price a usage event, accumulate spend, and return the run check."""
        model_identity = report.model_identity
        if model_identity is None:
            raise MissingModelIdentityError()
        cost = self._pricing.price(
            model_identity, report.input_tokens, report.output_tokens
        )
        day = self._today()
        self._store.add_run_spend(report.run_id, cost, ctx=ctx)
        self._store.add_day_spend(workload.name, day, cost, ctx=ctx)
        if workload.team is not None:
            self._store.add_team_spend(workload.team, day, cost, ctx=ctx)
        self._store.record_attribution(
            CostAttribution(
                run_id=report.run_id,
                workload=workload.name,
                team=workload.team,
                model_identity=model_identity,
                input_tokens=report.input_tokens,
                output_tokens=report.output_tokens,
                tool_calls=report.tool_calls,
                cost_usd=cost,
                timestamp=report.timestamp,
                tenant_id=ctx.tenant_id,
                attribution_key=ctx.attribution_key,
            ),
            ctx=ctx,
        )
        if self._cost_service is not None:
            self._cost_service.record(
                CostEvent(
                    event_id=f"budget-{report.run_id}-{uuid4().hex[:12]}",
                    tenant_id=ctx.tenant_id,
                    team_id=workload.team or ctx.team_id or DEFAULT_TEAM_ID,
                    workload_id=workload.name,
                    run_id=report.run_id,
                    cost_type=CostType.LLM,
                    model=model_identity,
                    cost_usd=cost,
                    completed=False,
                    retry=False,
                    escalation=False,
                    occurred_at=report.timestamp,
                ),
                ctx=ctx,
            )
        metrics.get_metrics().record_spend(
            workload=workload.name,
            team=workload.team,
            model=model_identity,
            cost_usd=cost,
        )

        budget = workload.spec.budget
        run_spent = self._store.run_spend(report.run_id, ctx=ctx)
        if run_spent > budget.per_run_usd:
            metrics.get_metrics().record_budget_exceeded(
                workload=workload.name, team=workload.team, level=BudgetLevel.RUN.value
            )
            return BudgetOutcome(
                check=self._exceeded(BudgetLevel.RUN, budget.per_run_usd), cost_usd=cost
            )
        day_spent = self._store.day_spend(workload.name, day, ctx=ctx)
        if day_spent > budget.per_day_usd:
            metrics.get_metrics().record_budget_exceeded(
                workload=workload.name, team=workload.team, level=BudgetLevel.DAY.value
            )
            return BudgetOutcome(
                check=self._exceeded(BudgetLevel.DAY, budget.per_day_usd), cost_usd=cost
            )
        team_limit = budget.per_team_usd
        if team_limit is not None and workload.team is not None:
            team_spent = self._store.team_spend(workload.team, day, ctx=ctx)
            if team_spent > team_limit:
                metrics.get_metrics().record_budget_exceeded(
                    workload=workload.name, team=workload.team, level=BudgetLevel.TEAM.value
                )
                return BudgetOutcome(
                    check=self._exceeded(BudgetLevel.TEAM, team_limit), cost_usd=cost
                )
        cap = self._tenant_cap(ctx)
        if cap is not None:
            metrics.get_metrics().record_budget_exceeded(
                workload=workload.name, team=workload.team, level=BudgetLevel.DAY.value
            )
            return BudgetOutcome(check=cap, cost_usd=cost)
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

    def snapshot(
        self,
        workload: AgentWorkload,
        run_id: str,
        *,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> BudgetSnapshot:
        """Return current spend for a run, workload day, and team day."""
        day = self._today()
        team = workload.team
        return BudgetSnapshot(
            workload=workload.name,
            team=team,
            day=day,
            run_usd=self._store.run_spend(run_id, ctx=ctx),
            day_usd=self._store.day_spend(workload.name, day, ctx=ctx),
            team_usd=(
                self._store.team_spend(team, day, ctx=ctx) if team is not None else 0.0
            ),
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
