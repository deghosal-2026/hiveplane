"""Edge-path tests for the progressive store (M37/M38 coverage)."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Engine

from hiveplane.progressive.models import (
    CanaryArm,
    CanaryRollout,
    CanarySample,
    CanaryState,
    ExperimentArm,
    ExperimentCampaign,
    ExperimentState,
    ShadowRun,
    ShadowStatus,
)
from hiveplane.progressive.store import (
    InMemoryProgressiveStore,
    PostgresProgressiveStore,
)
from hiveplane.tenancy import TenantContext
from postgres import ensure_schema

_NOW = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)
_OTHER = TenantContext(tenant_id="other")


def _shadow(shadow_id: str = "shadow-1") -> ShadowRun:
    return ShadowRun(
        shadow_run_id=shadow_id,
        candidate_workload_id="repo-agent",
        production_run_id="run-1",
        input_ref="run:run-1",
        budget_id="shadow",
        status=ShadowStatus.COMPLETED,
        created_at=_NOW,
    )


def _rollout(rollout_id: str = "canary-1") -> CanaryRollout:
    return CanaryRollout(
        rollout_id=rollout_id,
        workload_id="repo-agent",
        baseline_version=1,
        candidate_version=2,
        traffic_pct=10,
        window_seconds=60,
        min_sample=1,
        state=CanaryState.ACTIVE,
        window_start=_NOW,
        window_end=_NOW,
        created_at=_NOW,
    )


def _sample(sample_id: str = "sample-1") -> CanarySample:
    return CanarySample(
        sample_id=sample_id,
        rollout_id="canary-1",
        run_id="run-1",
        arm=CanaryArm.CANDIDATE,
        sampled_at=_NOW,
    )


def _campaign(campaign_id: str = "campaign-1") -> ExperimentCampaign:
    return ExperimentCampaign(
        campaign_id=campaign_id,
        workload_id="repo-agent",
        created_at=_NOW,
    )


def _arm(arm_id: str = "arm-1") -> ExperimentArm:
    return ExperimentArm(
        arm_id=arm_id,
        campaign_id="campaign-1",
        model_identity="gpt-4o",
        created_at=_NOW,
    )


def test_in_memory_progressive_edges() -> None:
    store = InMemoryProgressiveStore()
    store.add_canary_rollout(_rollout())
    store.add_canary_sample(_sample())
    store.add_campaign(_campaign())
    store.add_arm(_arm())

    assert store.get_canary_rollout("ghost") is None
    assert store.get_canary_rollout("canary-1", ctx=_OTHER) is None
    assert store.list_canary_rollouts(workload="docs-agent") == []
    assert store.get_campaign("ghost") is None
    assert store.get_campaign("campaign-1", ctx=_OTHER) is None
    assert store.list_campaigns(workload="docs-agent") == []
    assert store.get_arm("ghost") is None
    assert store.get_arm("arm-1", ctx=_OTHER) is None
    assert store.list_arms("ghost") == []
    assert store.list_canary_samples("canary-1")[0].sample_id == "sample-1"
    store.clear()
    assert store.list_canary_rollouts() == []


def test_postgres_progressive_edges(pg_engine: Engine) -> None:
    ensure_schema(pg_engine)
    store = PostgresProgressiveStore(pg_engine)
    store.clear()
    store.add_shadow_run(_shadow())
    store.add_canary_rollout(_rollout())
    store.add_canary_rollout(
        _rollout().model_copy(update={"state": CanaryState.PROMOTED})
    )  # update branch
    store.add_canary_sample(_sample())
    store.add_campaign(_campaign())
    store.add_campaign(
        _campaign().model_copy(update={"state": ExperimentState.COMPLETED})
    )  # update branch
    store.add_arm(_arm())
    store.add_arm(_arm().model_copy(update={"score": 0.9}))  # update branch

    assert store.get_shadow_run("ghost") is None
    assert store.list_shadow_runs(workload="repo-agent")[0].shadow_run_id == "shadow-1"
    assert store.get_canary_rollout("canary-1").state is CanaryState.PROMOTED  # type: ignore[union-attr]
    assert store.get_canary_rollout("ghost") is None
    assert store.list_canary_rollouts(workload="repo-agent")
    assert store.list_canary_samples("canary-1")[0].arm is CanaryArm.CANDIDATE
    assert store.get_campaign("campaign-1").state is ExperimentState.COMPLETED  # type: ignore[union-attr]
    assert store.get_campaign("ghost") is None
    assert store.list_campaigns(workload="repo-agent")
    assert store.get_arm("arm-1").score == 0.9  # type: ignore[union-attr]
    assert store.get_arm("ghost") is None
    assert store.list_arms("campaign-1")[0].score == 0.9
    store.clear()
    assert store.get_campaign("campaign-1") is None


def test_postgres_shadow_run_update_and_missing(pg_engine: Engine) -> None:
    ensure_schema(pg_engine)
    store = PostgresProgressiveStore(pg_engine)
    store.clear()
    store.add_shadow_run(_shadow())
    store.add_shadow_run(_shadow().model_copy(update={"status": ShadowStatus.FAILED}))

    assert store.get_shadow_run("shadow-1").status is ShadowStatus.FAILED  # type: ignore[union-attr]
    assert store.get_shadow_run("ghost") is None
    assert store.list_shadow_runs(budget_id="other") == []
    store.clear()
