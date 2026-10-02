"""Canary routing, evaluation, auto-promote, and auto-abort (M38-01..M38-05)."""

from __future__ import annotations

import builtins
import uuid
from collections.abc import Callable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Protocol

from pydantic import JsonValue

from hiveplane.core.run import Run, RunState
from hiveplane.execution.models import CanaryAssignment
from hiveplane.learning.sampling import sample_bucket
from hiveplane.progressive.errors import CanaryNotFoundError
from hiveplane.progressive.models import (
    CanaryArm,
    CanaryDecision,
    CanaryEvaluation,
    CanaryRollout,
    CanarySample,
    CanaryState,
)
from hiveplane.progressive.store import ProgressiveStore
from hiveplane.registry.service import RegistryService
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext
from hiveplane.tenancy.context import context_for_run

#: Automated transitions are attributed to this actor (D28).
AUTOMATION_ACTOR = "progressive-delivery"
#: A candidate judge mean below the baseline by more than this is a regression.
_JUDGE_REGRESSION_MARGIN = 0.2


class _Audit(Protocol):
    def append(
        self,
        actor: str,
        action: str,
        subject: str,
        *,
        detail: str | None = ...,
        ctx: TenantContext = ...,
    ) -> object: ...


class CanaryService:
    """Splits traffic to a candidate and promotes or aborts it on evidence."""

    def __init__(
        self,
        store: ProgressiveStore,
        *,
        registry: RegistryService,
        audit: _Audit | None = None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
        sample_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._registry = registry
        self._audit = audit
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: f"canary-{uuid.uuid4().hex[:12]}")
        self._sample_id_factory = sample_id_factory or (
            lambda: f"sample-{uuid.uuid4().hex[:12]}"
        )

    def start(
        self,
        workload: str,
        *,
        candidate_version: int,
        traffic_pct: int,
        window_seconds: int,
        min_sample: int,
        blast_radius_cap: int | None = None,
        eligible_rule: dict[str, JsonValue] | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> CanaryRollout:
        """Start a canary routing a percentage of eligible triggers to a candidate."""
        record = self._registry.get(workload, ctx=ctx)
        now = self._clock()
        rollout = CanaryRollout(
            rollout_id=self._id_factory(),
            workload_id=workload,
            baseline_version=record.current_version,
            candidate_version=candidate_version,
            traffic_pct=traffic_pct,
            eligible_rule=eligible_rule or {},
            window_seconds=window_seconds,
            min_sample=min_sample,
            blast_radius_cap=blast_radius_cap,
            state=CanaryState.ACTIVE,
            window_start=now,
            window_end=now + timedelta(seconds=window_seconds),
            created_at=now,
            tenant_id=record.tenant_id,
        )
        self._store.add_canary_rollout(rollout, ctx=ctx)
        self._record("canary.started", rollout, reason="started", ctx=ctx)
        return rollout

    def get(
        self, rollout_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> CanaryRollout:
        """Return a rollout, or raise when absent/out of scope."""
        rollout = self._store.get_canary_rollout(rollout_id, ctx=ctx)
        if rollout is None:
            raise CanaryNotFoundError(rollout_id)
        return rollout

    def list(
        self, *, workload: str | None = None, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[CanaryRollout]:
        """List canary rollouts, optionally filtered."""
        return self._store.list_canary_rollouts(workload=workload, ctx=ctx)

    def active_for(
        self, workload: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> CanaryRollout | None:
        """Return the workload's active rollout, if any."""
        for rollout in self.list(workload=workload, ctx=ctx):
            if rollout.state is CanaryState.ACTIVE:
                return rollout
        return None

    def route(self, run: Run) -> CanaryAssignment | None:
        """Pick the arm for a production run, or ``None`` when no canary is active.

        Called by the run service at admission; the chosen arm is stored on the
        run so the terminal outcome can be sampled back to the rollout.
        """
        ctx = context_for_run(run.tenant_id, run.team_id, run.attribution_key)
        rollout = self.active_for(run.workload_id, ctx=ctx)
        if rollout is None:
            return None
        arm = self.select_arm(
            rollout.rollout_id,
            run.id,
            eligible=self.is_eligible(rollout, run.task),
            ctx=ctx,
        )
        return CanaryAssignment(
            rollout_id=rollout.rollout_id,
            arm=arm.value,
            candidate_version=rollout.candidate_version,
        )

    def record(self, run: Run) -> None:
        """Record the terminal outcome of a canary-routed run as a sample.

        Once the rollout window has elapsed, the completed sample drives the
        automated promote/abort decision.
        """
        if run.canary_rollout_id is None or run.canary_arm is None:
            return
        ctx = context_for_run(run.tenant_id, run.team_id, run.attribution_key)
        self.record_sample(
            run.canary_rollout_id,
            run.id,
            arm=CanaryArm(run.canary_arm),
            metrics={
                "error": run.state is RunState.FAILED,
                "cost_usd": run.cost_usd,
            },
            ctx=ctx,
        )
        rollout = self.get(run.canary_rollout_id, ctx=ctx)
        if rollout.state is CanaryState.ACTIVE and self._clock() >= rollout.window_end:
            self.auto_decide(rollout.rollout_id, ctx=ctx)

    def is_eligible(self, rollout: CanaryRollout, attributes: Mapping[str, JsonValue]) -> bool:
        """Return True when a trigger's attributes satisfy the canary's rule."""
        return all(attributes.get(key) == value for key, value in rollout.eligible_rule.items())

    def select_arm(
        self,
        rollout_id: str,
        run_id: str,
        *,
        eligible: bool = True,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> CanaryArm:
        """Deterministically choose the arm for a run (candidate at ``traffic_pct``)."""
        rollout = self.get(rollout_id, ctx=ctx)
        if rollout.state is not CanaryState.ACTIVE or not eligible:
            return CanaryArm.BASELINE
        if rollout.blast_radius_cap is not None:
            candidate_count = sum(
                1
                for sample in self._store.list_canary_samples(rollout_id, ctx=ctx)
                if sample.arm is CanaryArm.CANDIDATE
            )
            if candidate_count >= rollout.blast_radius_cap:
                return CanaryArm.BASELINE
        return (
            CanaryArm.CANDIDATE
            if sample_bucket(run_id) < rollout.traffic_pct
            else CanaryArm.BASELINE
        )

    def record_sample(
        self,
        rollout_id: str,
        run_id: str,
        *,
        arm: CanaryArm,
        metrics: dict[str, JsonValue] | None = None,
        judge_score: float | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> CanarySample:
        """Record a sampled run's metrics (and optional judge score) for an arm."""
        rollout = self.get(rollout_id, ctx=ctx)
        sample = CanarySample(
            sample_id=self._sample_id_factory(),
            rollout_id=rollout_id,
            run_id=run_id,
            arm=arm,
            metrics=metrics or {},
            judge_score=judge_score,
            sampled_at=self._clock(),
            tenant_id=rollout.tenant_id,
        )
        self._store.add_canary_sample(sample, ctx=ctx)
        return sample

    def evaluate(
        self, rollout_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> CanaryEvaluation:
        """Compare candidate and baseline over the rollout window."""
        rollout = self.get(rollout_id, ctx=ctx)
        samples = self._store.list_canary_samples(rollout_id, ctx=ctx)
        baseline = [s for s in samples if s.arm is CanaryArm.BASELINE]
        candidate = [s for s in samples if s.arm is CanaryArm.CANDIDATE]
        baseline_error = _error_rate(baseline)
        candidate_error = _error_rate(candidate)
        baseline_judge = _judge_mean(baseline)
        candidate_judge = _judge_mean(candidate)
        sample_reached = (
            len(candidate) >= rollout.min_sample and len(baseline) >= rollout.min_sample
        )
        blast_radius_exceeded = (
            rollout.blast_radius_cap is not None
            and len(candidate) > rollout.blast_radius_cap
        )
        regression = candidate_error > baseline_error + 1e-9 or (
            baseline_judge is not None
            and candidate_judge is not None
            and candidate_judge < baseline_judge - _JUDGE_REGRESSION_MARGIN
        )
        ready = sample_reached and not regression and not blast_radius_exceeded
        reason = _evaluation_reason(
            sample_reached, blast_radius_exceeded, regression, len(candidate), rollout.min_sample
        )
        return CanaryEvaluation(
            rollout_id=rollout_id,
            state=rollout.state,
            baseline_samples=len(baseline),
            candidate_samples=len(candidate),
            baseline_error_rate=baseline_error,
            candidate_error_rate=candidate_error,
            baseline_judge_mean=baseline_judge,
            candidate_judge_mean=candidate_judge,
            sample_reached=sample_reached,
            blast_radius_exceeded=blast_radius_exceeded,
            regression=regression,
            ready=ready,
            reason=reason,
        )

    def auto_decide(
        self, rollout_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> CanaryDecision:
        """Promote a clean canary, abort a regressing one, or wait for more samples."""
        evaluation = self.evaluate(rollout_id, ctx=ctx)
        if evaluation.regression:
            self.abort(
                rollout_id,
                operator=AUTOMATION_ACTOR,
                reason=evaluation.reason or "regression detected",
                ctx=ctx,
            )
            return CanaryDecision(
                rollout_id=rollout_id,
                action="abort",
                actor=AUTOMATION_ACTOR,
                reason=evaluation.reason,
            )
        if evaluation.ready:
            self.promote(
                rollout_id,
                operator=AUTOMATION_ACTOR,
                reason=evaluation.reason or "clean window",
                ctx=ctx,
            )
            return CanaryDecision(
                rollout_id=rollout_id,
                action="promote",
                actor=AUTOMATION_ACTOR,
                reason=evaluation.reason,
            )
        return CanaryDecision(
            rollout_id=rollout_id,
            action="none",
            actor=AUTOMATION_ACTOR,
            reason=evaluation.reason,
        )

    def decide_due(
        self, *, at: datetime | None = None, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> builtins.list[CanaryDecision]:
        """Auto-decide every active rollout whose window has elapsed (M38-05)."""
        now = at or self._clock()
        decisions: builtins.list[CanaryDecision] = []
        for rollout in self.list(ctx=ctx):
            if rollout.state is CanaryState.ACTIVE and now >= rollout.window_end:
                decisions.append(self.auto_decide(rollout.rollout_id, ctx=ctx))
        return decisions

    def promote(
        self,
        rollout_id: str,
        *,
        operator: str = AUTOMATION_ACTOR,
        reason: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> CanaryRollout:
        """Promote the candidate and re-point traffic without manual action."""
        rollout = self.get(rollout_id, ctx=ctx)
        self._registry.promote(rollout.workload_id, rollout.candidate_version, ctx=ctx)
        updated = rollout.model_copy(update={"state": CanaryState.PROMOTED})
        self._store.add_canary_rollout(updated, ctx=ctx)
        self._record("canary.promoted", updated, reason=reason, ctx=ctx, actor=operator)
        return updated

    def abort(
        self,
        rollout_id: str,
        *,
        operator: str = AUTOMATION_ACTOR,
        reason: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> CanaryRollout:
        """Roll traffic back to the baseline and mark the candidate quarantined."""
        rollout = self.get(rollout_id, ctx=ctx)
        self._registry.promote(rollout.workload_id, rollout.baseline_version, ctx=ctx)
        updated = rollout.model_copy(
            update={"state": CanaryState.ROLLED_BACK, "candidate_quarantined": True}
        )
        self._store.add_canary_rollout(updated, ctx=ctx)
        self._record("canary.aborted", updated, reason=reason, ctx=ctx, actor=operator)
        return updated

    def _record(
        self,
        action: str,
        rollout: CanaryRollout,
        *,
        reason: str | None,
        ctx: TenantContext,
        actor: str = AUTOMATION_ACTOR,
    ) -> None:
        if self._audit is not None:
            self._audit.append(
                actor,
                action,
                rollout.rollout_id,
                detail=f"{rollout.workload_id} v{rollout.candidate_version}: {reason or ''}",
                ctx=ctx,
            )


def _error_rate(samples: list[CanarySample]) -> float:
    if not samples:
        return 0.0
    return sum(1 for sample in samples if sample.metrics.get("error")) / len(samples)


def _judge_mean(samples: list[CanarySample]) -> float | None:
    scored = [sample.judge_score for sample in samples if sample.judge_score is not None]
    if not scored:
        return None
    return sum(scored) / len(scored)


def _evaluation_reason(
    sample_reached: bool,
    blast_radius_exceeded: bool,
    regression: bool,
    candidate_samples: int,
    min_sample: int,
) -> str:
    if regression:
        return "candidate regressed against baseline"
    if blast_radius_exceeded:
        return "blast-radius cap exceeded"
    if not sample_reached:
        return f"insufficient samples ({candidate_samples}/{min_sample})"
    return "clean window"
