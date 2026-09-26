"""Certification domain models (DD-09..DD-12).

These models describe the certification pipeline data: benchmark corpora and
tasks, thresholds and policy, evaluations, signed attestations, and the status
state machine. Runtime enforcement lives in later milestones.
"""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    field_validator,
    model_validator,
)

from hiveplane.certification.binding import ArtifactBinding
from hiveplane.core.types import Duration


class CertificationStatus(StrEnum):
    """Where a workload stands in the certification lifecycle."""

    UNCERTIFIED = "uncertified"
    PROVISIONAL = "provisional"
    CERTIFIED = "certified"
    QUARANTINED = "quarantined"


class CertificationEvent(StrEnum):
    """Events that drive certification status transitions."""

    STAGING_PASS = "staging_pass"
    PRODUCTION_PASS = "production_pass"
    RECERT_FAIL = "recert_fail"
    DRIFT = "drift"
    RECOVER = "recover"


class TargetContext(StrEnum):
    """The context a certification is scoped to."""

    STAGING = "staging"
    PRODUCTION = "production"


class CheckType(StrEnum):
    """Deterministic and model-evaluated benchmark check kinds."""

    EXACT_MATCH = "exact_match"
    ACTION_AUDIT = "action_audit"
    SCHEMA_MATCH = "schema_match"
    RUBRIC = "rubric"
    CUSTOM = "custom"


class Thresholds(BaseModel):
    """Pass/fail thresholds scoped to a target context."""

    model_config = ConfigDict(extra="forbid")

    min_pass_rate: float = Field(ge=0.0, le=1.0)
    max_critical_failures: int = Field(ge=0)
    max_p95_latency_ms: int = Field(gt=0)
    min_production_runs_survived: int = Field(default=0, ge=0)


class EvalSummary(BaseModel):
    """Aggregate evaluation results for a benchmark run."""

    model_config = ConfigDict(extra="forbid")

    pass_rate: float = Field(ge=0.0, le=1.0)
    critical_failures: int = Field(ge=0)
    p95_latency_ms: int = Field(ge=0)
    tasks_passed: int = Field(ge=0)
    tasks_failed: int = Field(ge=0)


class Environment(BaseModel):
    """Versions of the components that produced a benchmark result."""

    model_config = ConfigDict(extra="forbid")

    sandbox_image: str = Field(min_length=1)
    runtime_adapter: str = Field(min_length=1)
    control_plane_version: str = Field(min_length=1)


class Signer(BaseModel):
    """The identity and signature that authenticate an attestation."""

    model_config = ConfigDict(extra="forbid")

    identity: str = Field(min_length=1)
    key_id: str = Field(min_length=1)
    signature: str = Field(min_length=1)


class Attestation(BaseModel):
    """A signed, immutable certification record."""

    model_config = ConfigDict(extra="forbid")

    attestation_id: str = Field(min_length=1)
    workload_id: str = Field(min_length=1)
    manifest_version: int = Field(ge=1)
    benchmark_version: str = Field(min_length=1)
    benchmark_run_id: str = Field(min_length=1)
    corpus_id: str = Field(min_length=1)
    corpus_version: int = Field(ge=1)
    model_identity: str = Field(min_length=1)
    status: CertificationStatus
    target_context: TargetContext
    eval_summary: EvalSummary
    timestamp: datetime
    environment: Environment
    signer: Signer
    artifact_hash: str | None = None
    binding: ArtifactBinding | None = None
    previous_attestation_id: str | None = None

    @field_validator("timestamp")
    @classmethod
    def _timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return value


class Certification(BaseModel):
    """The outcome of evaluating a benchmark run against thresholds."""

    model_config = ConfigDict(extra="forbid")

    certification_id: str = Field(min_length=1)
    workload_id: str = Field(min_length=1)
    manifest_version: int = Field(ge=1)
    status: CertificationStatus
    target_context: TargetContext
    benchmark_run_id: str = Field(min_length=1)
    thresholds: Thresholds
    eval_summary: EvalSummary | None = None
    timestamp: datetime
    attestation_id: str | None = None

    @field_validator("timestamp")
    @classmethod
    def _timestamp_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("timestamp must be timezone-aware")
        return value


class ExpectedOutcome(BaseModel):
    """The expected result of a benchmark task."""

    model_config = ConfigDict(extra="forbid")

    outcome: str | None = None
    required_fields: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)


class BenchmarkTaskCheck(BaseModel):
    """A deterministic or evaluator-scored pass/fail check for a task."""

    model_config = ConfigDict(extra="forbid")

    type: CheckType
    field: str | None = None
    value: JsonValue | None = None
    required_actions: list[str] = Field(default_factory=list)
    forbidden_actions: list[str] = Field(default_factory=list)
    criteria: list[str] = Field(default_factory=list)
    min_score: int | None = Field(default=None, ge=0)
    callable: str | None = None

    @model_validator(mode="after")
    def _check_type_requirements(self) -> BenchmarkTaskCheck:
        if self.type is CheckType.EXACT_MATCH and (not self.field or self.value is None):
            raise ValueError("exact_match check requires 'field' and 'value'")
        if self.type is CheckType.ACTION_AUDIT and not (
            self.required_actions or self.forbidden_actions
        ):
            raise ValueError(
                "action_audit check requires 'required_actions' or 'forbidden_actions'"
            )
        if self.type is CheckType.RUBRIC and (not self.criteria or self.min_score is None):
            raise ValueError("rubric check requires 'criteria' and 'min_score'")
        if self.type is CheckType.CUSTOM and not self.callable:
            raise ValueError("custom check requires 'callable'")
        return self


class BenchmarkTask(BaseModel):
    """A single reproducible benchmark task."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    input: dict[str, JsonValue] = Field(default_factory=dict)
    expected: ExpectedOutcome | None = None
    check: BenchmarkTaskCheck
    critical: bool = False
    timeout_seconds: int = Field(default=30, gt=0)
    allow_network: bool = False


class BenchmarkCorpus(BaseModel):
    """A versioned set of benchmark tasks for a workload type."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    version: int = Field(ge=1)
    tasks: list[BenchmarkTask] = Field(min_length=1)

    @model_validator(mode="after")
    def _task_ids_must_be_unique(self) -> BenchmarkCorpus:
        ids = [task.id for task in self.tasks]
        if len(ids) != len(set(ids)):
            raise ValueError("benchmark task ids must be unique within a corpus")
        return self


class CertificationPolicy(BaseModel):
    """Certification thresholds, re-cert cadence, and drift tolerance."""

    model_config = ConfigDict(extra="forbid")

    staging: Thresholds
    production: Thresholds
    re_cert_interval: Duration = 14 * 86400
    grace_margin: float = Field(default=0.05, ge=0.0, lt=0.5)

    @model_validator(mode="after")
    def _production_not_weaker_than_staging(self) -> CertificationPolicy:
        if self.production.min_pass_rate < self.staging.min_pass_rate:
            raise ValueError(
                "production threshold must be at least the staging threshold: "
                f"production.min_pass_rate={self.production.min_pass_rate} < "
                f"staging.min_pass_rate={self.staging.min_pass_rate}"
            )
        if self.staging.max_critical_failures < self.production.max_critical_failures:
            raise ValueError(
                "production threshold must not allow more critical failures than staging"
            )
        return self


class CheckStatus(StrEnum):
    """Pass/fail outcome of a single benchmark task."""

    PASS = "pass"
    FAIL = "fail"


class Severity(StrEnum):
    """How serious a regression is (DD-11, M33)."""

    NONE = "none"
    WARNING = "warning"
    CRITICAL = "critical"


class ReplayFrameSet(BaseModel):
    """A replayable reference for one benchmark task (D18, M33-03).

    The frame set links a regressed/improved task to its deterministic task
    contract (input, expected outcome, and check) and the trace id of the run,
    so the exact task can be replayed against the fake provider.
    """

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    trace_id: str | None = None
    input: dict[str, JsonValue] = Field(default_factory=dict)
    expected: dict[str, JsonValue] = Field(default_factory=dict)
    check: dict[str, JsonValue] = Field(default_factory=dict)
    replay_ref: str = Field(min_length=1)
    replayable: bool = True


class BenchmarkTaskResult(BaseModel):
    """The result of executing and checking one benchmark task."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    status: CheckStatus
    latency_ms: int = Field(ge=0)
    tokens: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0.0)
    trace_id: str | None = None
    critical: bool = False
    failure_reason: str | None = None


class BenchmarkAggregate(BaseModel):
    """Aggregate metrics across every task in a benchmark run."""

    model_config = ConfigDict(extra="forbid")

    total: int = Field(ge=0)
    passed: int = Field(ge=0)
    failed: int = Field(ge=0)
    pass_rate: float = Field(ge=0.0, le=1.0)
    critical_failures: int = Field(ge=0)
    p50_latency_ms: int = Field(ge=0)
    p95_latency_ms: int = Field(ge=0)
    total_tokens: int = Field(ge=0)


class BenchmarkResult(BaseModel):
    """The structured, reproducible result of running a corpus."""

    model_config = ConfigDict(extra="forbid")

    benchmark_run_id: str = Field(min_length=1)
    workload_id: str = Field(min_length=1)
    manifest_version: int = Field(ge=1)
    corpus_id: str = Field(min_length=1)
    corpus_version: int = Field(ge=1)
    model_identity: str = Field(min_length=1)
    environment: Environment
    started_at: datetime
    finished_at: datetime
    tasks: list[BenchmarkTaskResult] = Field(default_factory=list)
    aggregate: BenchmarkAggregate

    def to_eval_summary(self) -> EvalSummary:
        """Return the certification-facing summary for this result."""
        return EvalSummary(
            pass_rate=self.aggregate.pass_rate,
            critical_failures=self.aggregate.critical_failures,
            p95_latency_ms=self.aggregate.p95_latency_ms,
            tasks_passed=self.aggregate.passed,
            tasks_failed=self.aggregate.failed,
        )


class TaskDelta(BaseModel):
    """A change in one task's pass/fail status between two results."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    before: CheckStatus
    after: CheckStatus
    failure_reason: str | None = None
    critical: bool = False
    severity: Severity = Severity.NONE
    latency_delta_ms: int = 0
    tokens_delta: int = 0
    cost_delta_usd: float = 0.0
    replay: ReplayFrameSet | None = None


class RegressionDiff(BaseModel):
    """Task-level regression comparison between two certifications (DD-11)."""

    model_config = ConfigDict(extra="forbid")

    workload_id: str = Field(min_length=1)
    before_attestation_id: str = Field(min_length=1)
    after_attestation_id: str = Field(min_length=1)
    total: int = Field(ge=0)
    passed_before: int = Field(ge=0)
    passed_after: int = Field(ge=0)
    regressed: list[TaskDelta] = Field(default_factory=list)
    improved: list[TaskDelta] = Field(default_factory=list)
    added: list[str] = Field(default_factory=list)
    removed: list[str] = Field(default_factory=list)
    blocked: bool
    severity: Severity = Severity.NONE
    critical_regressions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
    baseline_attestation_id: str | None = None
    summary: str = ""


class RegressionReport(BaseModel):
    """A machine-readable and human-readable regression artifact (M33-05)."""

    model_config = ConfigDict(extra="forbid")

    workload_id: str = Field(min_length=1)
    before_attestation_id: str = Field(min_length=1)
    after_attestation_id: str = Field(min_length=1)
    severity: Severity
    blocked: bool
    machine_report: dict[str, JsonValue] = Field(default_factory=dict)
    human_report: str = ""
    generated_at: datetime

    @field_validator("generated_at")
    @classmethod
    def _generated_at_must_be_timezone_aware(cls, value: datetime) -> datetime:
        if value.tzinfo is None:
            raise ValueError("generated_at must be timezone-aware")
        return value


class CertificationRecord(BaseModel):
    """A certification outcome bundled with its attestation and benchmark result."""

    model_config = ConfigDict(extra="forbid")

    record_id: str = Field(min_length=1)
    certification: Certification
    attestation: Attestation
    benchmark_result: BenchmarkResult
    regression_report: RegressionReport | None = None


_TRANSITIONS: dict[CertificationStatus, dict[CertificationEvent, CertificationStatus]] = {
    CertificationStatus.UNCERTIFIED: {
        CertificationEvent.STAGING_PASS: CertificationStatus.PROVISIONAL,
    },
    CertificationStatus.PROVISIONAL: {
        CertificationEvent.PRODUCTION_PASS: CertificationStatus.CERTIFIED,
        CertificationEvent.RECERT_FAIL: CertificationStatus.QUARANTINED,
    },
    CertificationStatus.CERTIFIED: {
        CertificationEvent.PRODUCTION_PASS: CertificationStatus.CERTIFIED,
        CertificationEvent.RECERT_FAIL: CertificationStatus.QUARANTINED,
        CertificationEvent.DRIFT: CertificationStatus.QUARANTINED,
    },
    CertificationStatus.QUARANTINED: {
        CertificationEvent.RECOVER: CertificationStatus.PROVISIONAL,
    },
}


def advance_status(current: CertificationStatus, event: CertificationEvent) -> CertificationStatus:
    """Apply a certification event to the current status.

    Raises:
        ValueError: if the transition is not permitted by the lifecycle.
    """
    target = _TRANSITIONS.get(current, {}).get(event)
    if target is None:
        raise ValueError(
            f"invalid transition: {current.value} cannot handle event {event.value}"
        )
    return target
