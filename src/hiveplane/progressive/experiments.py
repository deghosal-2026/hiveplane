"""Model experiment campaigns: benchmark-score arms and select a winner (M38-06)."""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime

from hiveplane.progressive.errors import CanaryNotAllowedError, ExperimentNotFoundError
from hiveplane.progressive.models import (
    ExperimentArm,
    ExperimentCampaign,
    ExperimentState,
)
from hiveplane.progressive.store import ProgressiveStore
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext


class ExperimentService:
    """Runs multi-arm model experiments scored by the benchmark."""

    def __init__(
        self,
        store: ProgressiveStore,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
        arm_id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: f"campaign-{uuid.uuid4().hex[:12]}")
        self._arm_id_factory = arm_id_factory or (lambda: f"arm-{uuid.uuid4().hex[:12]}")

    def start(
        self,
        workload: str,
        arms: list[str],
        *,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> ExperimentCampaign:
        """Start a campaign routing across >= 2 model configurations."""
        if len(arms) < 2:
            raise CanaryNotAllowedError("an experiment requires at least two arms")
        now = self._clock()
        campaign = ExperimentCampaign(
            campaign_id=self._id_factory(),
            workload_id=workload,
            state=ExperimentState.RUNNING,
            created_at=now,
            tenant_id=ctx.tenant_id,
        )
        self._store.add_campaign(campaign, ctx=ctx)
        for model_identity in arms:
            self._store.add_arm(
                ExperimentArm(
                    arm_id=self._arm_id_factory(),
                    campaign_id=campaign.campaign_id,
                    model_identity=model_identity,
                    created_at=now,
                    tenant_id=ctx.tenant_id,
                ),
                ctx=ctx,
            )
        return campaign

    def get(
        self, campaign_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> ExperimentCampaign:
        """Return a campaign, or raise when absent/out of scope."""
        campaign = self._store.get_campaign(campaign_id, ctx=ctx)
        if campaign is None:
            raise ExperimentNotFoundError(campaign_id)
        return campaign

    def arms(
        self, campaign_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> list[ExperimentArm]:
        """Return the arms of a campaign."""
        self.get(campaign_id, ctx=ctx)
        return self._store.list_arms(campaign_id, ctx=ctx)

    def record_result(
        self,
        arm_id: str,
        *,
        benchmark_run_id: str,
        score: float | None,
        metrics: dict[str, object] | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> ExperimentArm:
        """Record an arm's benchmark run, score, and metrics."""
        arm = self._store.get_arm(arm_id, ctx=ctx)
        if arm is None:
            raise ExperimentNotFoundError(arm_id)
        updated = arm.model_copy(
            update={
                "benchmark_run_id": benchmark_run_id,
                "score": score,
                "metrics": metrics or {},
            }
        )
        self._store.add_arm(updated, ctx=ctx)
        return updated

    def select_winner(
        self, campaign_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> ExperimentCampaign:
        """Select the highest-scoring completed arm with recorded evidence."""
        campaign = self.get(campaign_id, ctx=ctx)
        scored = [
            arm
            for arm in self._store.list_arms(campaign_id, ctx=ctx)
            if arm.score is not None
        ]
        if not scored:
            updated = campaign.model_copy(
                update={
                    "state": ExperimentState.COMPLETED,
                    "rationale": "no arm produced a benchmark score; no winner selected",
                }
            )
            self._store.add_campaign(updated, ctx=ctx)
            return updated
        winner = max(scored, key=lambda arm: arm.score or 0.0)
        best = winner.score or 0.0
        updated = campaign.model_copy(
            update={
                "state": ExperimentState.COMPLETED,
                "winner_arm_id": winner.arm_id,
                "rationale": (
                    f"{winner.model_identity} scored {best:.3f} "
                    f"(benchmark {winner.benchmark_run_id})"
                ),
            }
        )
        self._store.add_campaign(updated, ctx=ctx)
        return updated
