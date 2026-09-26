"""Guard manager: coordinates runtime guards and emits policy-shaped events (M41-06/07)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from pydantic import BaseModel, ConfigDict, Field

from hiveplane.core.run import Run
from hiveplane.core.usage import UsageReport
from hiveplane.guards.context import ContextBudgetGuard
from hiveplane.guards.models import GuardAction, GuardEvent, GuardKind
from hiveplane.guards.velocity import SpendVelocityGuard
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext

GuardLimitsLookup = Callable[[str], "GuardLimits | None"]


class GuardLimits(BaseModel):
    """Per-workload guard limits."""

    model_config = ConfigDict(extra="forbid")

    context_tokens: int | None = Field(default=None, ge=1)
    context_warn_at: float = Field(default=0.8, ge=0.0, le=1.0)
    velocity_window_seconds: int = Field(default=300, gt=0)
    velocity_limit_usd: float | None = Field(default=None, gt=0.0)
    velocity_multiplier: float | None = Field(default=None, gt=0.0)


class GuardManager:
    """Runs the context and velocity guards over a run's usage."""

    def __init__(
        self,
        context: ContextBudgetGuard,
        velocity: SpendVelocityGuard,
        *,
        limit_lookup: GuardLimitsLookup | None = None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._context = context
        self._velocity = velocity
        self._limit_lookup = limit_lookup
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: f"guard-{uuid.uuid4().hex[:12]}")

    def on_usage(
        self,
        run: Run,
        report: UsageReport,
        *,
        budget_remaining_usd: float | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> GuardEvent | None:
        """Record usage and return the first guard breach, if any."""
        limits = self._limit_lookup(run.workload_id) if self._limit_lookup else None
        if limits is None:
            return None
        context_event = self._check_context(run, report, limits, ctx=ctx)
        if context_event is not None:
            return context_event
        return self._check_velocity(
            run, report, limits, budget_remaining_usd=budget_remaining_usd, ctx=ctx
        )

    def _check_context(
        self,
        run: Run,
        report: UsageReport,
        limits: GuardLimits,
        *,
        ctx: TenantContext,
    ) -> GuardEvent | None:
        self._context.record(
            run.id,
            step=len(self._context.accounts(run.id)),
            model_identity=report.model_identity or run.model_identity or "unspecified",
            input_tokens=report.input_tokens,
            output_tokens=report.output_tokens,
        )
        if limits.context_tokens is None:
            return None
        breach = self._context.check(
            run.id, limit=limits.context_tokens, warn_at=limits.context_warn_at
        )
        if breach is None:
            return None
        return GuardEvent(
            event_id=self._id_factory(),
            guard=GuardKind.CONTEXT,
            action=GuardAction.PAUSE,
            reason=breach.reason,
            rule_id=breach.rule_id,
            workload_id=run.workload_id,
            run_id=run.id,
            observed={
                "used": breach.used,
                "step": breach.step,
                "model_identity": breach.model_identity,
            },
            threshold={"limit": breach.limit},
            created_at=self._clock(),
            tenant_id=ctx.tenant_id,
        )

    def _check_velocity(
        self,
        run: Run,
        report: UsageReport,
        limits: GuardLimits,
        *,
        budget_remaining_usd: float | None,
        ctx: TenantContext,
    ) -> GuardEvent | None:
        self._velocity.record(run.workload_id, report.cost_usd)
        if limits.velocity_limit_usd is None and limits.velocity_multiplier is None:
            return None
        breach = self._velocity.check(
            run.workload_id,
            window_seconds=limits.velocity_window_seconds,
            limit_usd=limits.velocity_limit_usd or float("inf"),
            multiplier=limits.velocity_multiplier,
            budget_remaining_usd=budget_remaining_usd,
        )
        if breach is None:
            return None
        return GuardEvent(
            event_id=self._id_factory(),
            guard=GuardKind.VELOCITY,
            action=GuardAction.PAUSE,
            reason=breach.reason,
            rule_id=breach.rule_id,
            workload_id=run.workload_id,
            run_id=run.id,
            observed={
                "spent_usd": breach.spent_usd,
                "rate_per_minute": breach.rate_per_minute,
            },
            threshold={
                "limit_usd": breach.limit_usd,
                "window_seconds": breach.window_seconds,
            },
            created_at=self._clock(),
            tenant_id=ctx.tenant_id,
        )
