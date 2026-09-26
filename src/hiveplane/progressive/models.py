"""Typed records for progressive delivery — shadow, canary, and experiments (M37, M38)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

from hiveplane.tenancy.context import DEFAULT_TENANT_ID


class ShadowStatus(StrEnum):
    """Outcome of a shadow execution."""

    COMPLETED = "completed"
    FAILED = "failed"


class ShadowOutcome(BaseModel):
    """The evidence a shadow runner produces for a mirrored task."""

    model_config = ConfigDict(extra="forbid")

    status: ShadowStatus = ShadowStatus.COMPLETED
    result: JsonValue | None = None
    cost_usd: float = Field(default=0.0, ge=0.0)
    latency_ms: int = Field(default=0, ge=0)
    tool_calls: list[dict[str, JsonValue]] = Field(default_factory=list)
    policy_decisions: list[dict[str, JsonValue]] = Field(default_factory=list)
    failure_reason: str | None = None


class ShadowRun(BaseModel):
    """A candidate execution shadowing a production run (no delivery)."""

    model_config = ConfigDict(extra="forbid")

    shadow_run_id: str = Field(min_length=1)
    candidate_workload_id: str = Field(min_length=1)
    production_run_id: str = Field(min_length=1)
    input_ref: str = Field(min_length=1)
    candidate_version: int | None = Field(default=None, ge=1)
    result: JsonValue | None = None
    cost_usd: float = Field(default=0.0, ge=0.0)
    latency_ms: int = Field(default=0, ge=0)
    tool_calls: list[dict[str, JsonValue]] = Field(default_factory=list)
    policy_decisions: list[dict[str, JsonValue]] = Field(default_factory=list)
    budget_id: str = Field(default="shadow", min_length=1)
    status: ShadowStatus = ShadowStatus.COMPLETED
    failure_reason: str | None = None
    created_at: AwareDatetime
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)


class OutcomeDiff(BaseModel):
    """A production-vs-candidate comparison for a shadow run."""

    model_config = ConfigDict(extra="forbid")

    output_changed: bool
    production_output: JsonValue | None = None
    candidate_output: JsonValue | None = None
    cost_delta_usd: float = 0.0
    latency_delta_ms: int = 0
    tool_calls_added: list[dict[str, JsonValue]] = Field(default_factory=list)
    tool_calls_removed: list[dict[str, JsonValue]] = Field(default_factory=list)
    policy_decisions_added: list[dict[str, JsonValue]] = Field(default_factory=list)
    policy_decisions_removed: list[dict[str, JsonValue]] = Field(default_factory=list)


class ShadowReport(BaseModel):
    """The operator-facing outcome diff for a shadow run (M37-06)."""

    model_config = ConfigDict(extra="forbid")

    shadow_run_id: str = Field(min_length=1)
    production_run_id: str = Field(min_length=1)
    candidate_workload_id: str = Field(min_length=1)
    outcome_diff: OutcomeDiff


class CanaryState(StrEnum):
    """Lifecycle of a canary rollout."""

    PROPOSED = "proposed"
    ACTIVE = "active"
    PROMOTED = "promoted"
    ROLLED_BACK = "rolled_back"


class CanaryArm(StrEnum):
    """Which version served a sampled run."""

    BASELINE = "baseline"
    CANDIDATE = "candidate"


class CanaryRollout(BaseModel):
    """A percentage traffic split between a baseline and a candidate version."""

    model_config = ConfigDict(extra="forbid")

    rollout_id: str = Field(min_length=1)
    workload_id: str = Field(min_length=1)
    baseline_version: int = Field(ge=1)
    candidate_version: int = Field(ge=1)
    traffic_pct: int = Field(ge=0, le=100)
    eligible_rule: dict[str, JsonValue] = Field(default_factory=dict)
    window_seconds: int = Field(ge=0)
    min_sample: int = Field(ge=0)
    blast_radius_cap: int | None = Field(default=None, ge=0)
    state: CanaryState = CanaryState.ACTIVE
    window_start: AwareDatetime
    window_end: AwareDatetime
    candidate_quarantined: bool = False
    created_at: AwareDatetime
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)


class CanarySample(BaseModel):
    """A sampled run attributed to one canary arm."""

    model_config = ConfigDict(extra="forbid")

    sample_id: str = Field(min_length=1)
    rollout_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    arm: CanaryArm
    metrics: dict[str, JsonValue] = Field(default_factory=dict)
    judge_score: float | None = Field(default=None, ge=0.0, le=1.0)
    sampled_at: AwareDatetime
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)


class CanaryEvaluation(BaseModel):
    """The evaluated state of a canary window."""

    model_config = ConfigDict(extra="forbid")

    rollout_id: str = Field(min_length=1)
    state: CanaryState
    baseline_samples: int = Field(ge=0)
    candidate_samples: int = Field(ge=0)
    baseline_error_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    candidate_error_rate: float = Field(default=0.0, ge=0.0, le=1.0)
    baseline_judge_mean: float | None = None
    candidate_judge_mean: float | None = None
    sample_reached: bool = False
    blast_radius_exceeded: bool = False
    regression: bool = False
    ready: bool = False
    reason: str | None = None


class CanaryDecision(BaseModel):
    """The automated or manual decision applied to a canary rollout."""

    model_config = ConfigDict(extra="forbid")

    rollout_id: str = Field(min_length=1)
    action: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    reason: str | None = None


class ExperimentState(StrEnum):
    """Lifecycle of a model experiment campaign."""

    RUNNING = "running"
    COMPLETED = "completed"


class ExperimentCampaign(BaseModel):
    """A campaign comparing >= 2 model configurations by benchmark score (M38-06)."""

    model_config = ConfigDict(extra="forbid")

    campaign_id: str = Field(min_length=1)
    workload_id: str = Field(min_length=1)
    state: ExperimentState = ExperimentState.RUNNING
    winner_arm_id: str | None = None
    rationale: str | None = None
    created_at: AwareDatetime
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)


class ExperimentArm(BaseModel):
    """One model configuration arm of an experiment campaign."""

    model_config = ConfigDict(extra="forbid")

    arm_id: str = Field(min_length=1)
    campaign_id: str = Field(min_length=1)
    model_identity: str = Field(min_length=1)
    benchmark_run_id: str | None = None
    score: float | None = None
    metrics: dict[str, JsonValue] = Field(default_factory=dict)
    created_at: AwareDatetime
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
