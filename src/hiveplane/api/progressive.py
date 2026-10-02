"""Progressive delivery API: shadow, canary, and experiments (M37, M38)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from hiveplane.api.deps import (
    get_canary_service,
    get_experiment_service,
    get_run_service,
    get_shadow_service,
    get_tenant_context,
    require_permission,
)
from hiveplane.auth.models import OperatorIdentity, Permission
from hiveplane.core.run import Run
from hiveplane.execution.service import RunService
from hiveplane.progressive.canary import CanaryService
from hiveplane.progressive.experiments import ExperimentService
from hiveplane.progressive.models import (
    CanaryEvaluation,
    CanaryRollout,
    ExperimentArm,
    ExperimentCampaign,
    ShadowReport,
    ShadowRun,
)
from hiveplane.progressive.shadow import ShadowService
from hiveplane.tenancy.context import TenantContext

router = APIRouter(tags=["progressive"])

ShadowDep = Annotated[ShadowService, Depends(get_shadow_service)]
CanaryDep = Annotated[CanaryService, Depends(get_canary_service)]
ExperimentDep = Annotated[ExperimentService, Depends(get_experiment_service)]
RunDep = Annotated[RunService, Depends(get_run_service)]
TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]
FleetReader = Annotated[OperatorIdentity, Depends(require_permission(Permission.FLEET_READ))]
Promoter = Annotated[OperatorIdentity, Depends(require_permission(Permission.PROMOTE))]


class ShadowRequest(BaseModel):
    """Request body to start a shadow run."""

    model_config = ConfigDict(extra="forbid")

    candidate_workload_id: str = Field(min_length=1)
    production_run_id: str = Field(min_length=1)
    candidate_version: int | None = Field(default=None, ge=1)


class CanaryRequest(BaseModel):
    """Request body to start a canary rollout."""

    model_config = ConfigDict(extra="forbid")

    workload_id: str = Field(min_length=1)
    candidate_version: int = Field(ge=1)
    traffic_pct: int = Field(ge=0, le=100)
    window_seconds: int = Field(ge=0)
    min_sample: int = Field(ge=0)
    blast_radius_cap: int | None = Field(default=None, ge=0)
    eligible_rule: dict[str, JsonValue] = Field(default_factory=dict)


class DecisionRequest(BaseModel):
    """Request body for a manual canary override."""

    model_config = ConfigDict(extra="forbid")

    operator: str = Field(min_length=1)
    reason: str | None = None


class ExperimentRequest(BaseModel):
    """Request body to start a model experiment campaign."""

    model_config = ConfigDict(extra="forbid")

    workload_id: str = Field(min_length=1)
    arms: list[str] = Field(min_length=2)


@router.post("/shadow", response_model=ShadowRun, status_code=status.HTTP_201_CREATED)
def start_shadow(
    payload: ShadowRequest,
    shadows: ShadowDep,
    runs: RunDep,
    ctx: TenantDep,
    _: Promoter,
) -> ShadowRun:
    """Mirror a production run on a candidate without delivering the result."""
    production_run: Run = runs.get(payload.production_run_id, ctx=ctx)
    return shadows.start(
        production_run,
        candidate_workload_id=payload.candidate_workload_id,
        candidate_version=payload.candidate_version,
        ctx=ctx,
    )


@router.get("/shadow/{shadow_run_id}/report", response_model=ShadowReport)
def shadow_report(
    shadow_run_id: str, shadows: ShadowDep, runs: RunDep, ctx: TenantDep, _: FleetReader
) -> ShadowReport:
    """Return the production-vs-candidate outcome diff for a shadow run."""
    shadow = shadows.get(shadow_run_id, ctx=ctx)
    production_run = runs.get(shadow.production_run_id, ctx=ctx)
    return shadows.report(shadow_run_id, production_run, ctx=ctx)


@router.post("/canary", response_model=CanaryRollout, status_code=status.HTTP_201_CREATED)
def start_canary(
    payload: CanaryRequest, canaries: CanaryDep, ctx: TenantDep, _: Promoter
) -> CanaryRollout:
    """Start a canary routing a percentage of eligible triggers to a candidate."""
    return canaries.start(
        payload.workload_id,
        candidate_version=payload.candidate_version,
        traffic_pct=payload.traffic_pct,
        window_seconds=payload.window_seconds,
        min_sample=payload.min_sample,
        blast_radius_cap=payload.blast_radius_cap,
        eligible_rule=payload.eligible_rule,
        ctx=ctx,
    )


@router.get("/canary", response_model=list[CanaryRollout])
def list_canaries(
    canaries: CanaryDep,
    ctx: TenantDep,
    _: FleetReader,
    workload: str | None = None,
) -> list[CanaryRollout]:
    """List canary rollouts."""
    return canaries.list(workload=workload, ctx=ctx)


@router.get("/canary/{rollout_id}", response_model=CanaryEvaluation)
def canary_status(
    rollout_id: str, canaries: CanaryDep, ctx: TenantDep, _: FleetReader
) -> CanaryEvaluation:
    """Return a canary's state with window metrics and sample counts."""
    return canaries.evaluate(rollout_id, ctx=ctx)


@router.post("/canary/{rollout_id}/promote", response_model=CanaryRollout)
def promote_canary(
    rollout_id: str,
    payload: DecisionRequest,
    canaries: CanaryDep,
    ctx: TenantDep,
    _: Promoter,
) -> CanaryRollout:
    """Manually promote a canary candidate."""
    return canaries.promote(
        rollout_id, operator=payload.operator, reason=payload.reason, ctx=ctx
    )


@router.post("/canary/{rollout_id}/abort", response_model=CanaryRollout)
def abort_canary(
    rollout_id: str,
    payload: DecisionRequest,
    canaries: CanaryDep,
    ctx: TenantDep,
    _: Promoter,
) -> CanaryRollout:
    """Manually abort a canary and roll traffic back."""
    return canaries.abort(
        rollout_id, operator=payload.operator, reason=payload.reason, ctx=ctx
    )


@router.post("/experiments", response_model=ExperimentCampaign, status_code=status.HTTP_201_CREATED)
def start_experiment(
    payload: ExperimentRequest,
    experiments: ExperimentDep,
    ctx: TenantDep,
    _: Promoter,
) -> ExperimentCampaign:
    """Start a campaign routing across >= 2 model configurations."""
    return experiments.start(payload.workload_id, payload.arms, ctx=ctx)


@router.get("/experiments/{campaign_id}/arms", response_model=list[ExperimentArm])
def experiment_arms(
    campaign_id: str, experiments: ExperimentDep, ctx: TenantDep, _: FleetReader
) -> list[ExperimentArm]:
    """Return the arms (and scores) of an experiment campaign."""
    return experiments.arms(campaign_id, ctx=ctx)


@router.post("/experiments/{campaign_id}/select", response_model=ExperimentCampaign)
def select_experiment_winner(
    campaign_id: str, experiments: ExperimentDep, ctx: TenantDep, _: Promoter
) -> ExperimentCampaign:
    """Select the benchmark-best arm and record the rationale."""
    return experiments.select_winner(campaign_id, ctx=ctx)
