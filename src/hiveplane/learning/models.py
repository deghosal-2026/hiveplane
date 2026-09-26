"""Typed records for the learning loop (M36)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    model_validator,
)

from hiveplane.certification.models import (
    BenchmarkTask,
    BenchmarkTaskCheck,
    ExpectedOutcome,
)
from hiveplane.learning.errors import CandidateNotAllowedError
from hiveplane.tenancy.context import DEFAULT_TENANT_ID


class FeedbackVerdict(StrEnum):
    """How an operator judged a production run."""

    GOOD = "good"
    BAD = "bad"
    FAILED_WITH_LESSON = "failed-with-lesson"


class RunFeedback(BaseModel):
    """An attributable operator judgement of a terminal production run."""

    model_config = ConfigDict(extra="forbid")

    feedback_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    workload_id: str = Field(min_length=1)
    verdict: FeedbackVerdict
    notes: str = ""
    operator: str = Field(min_length=1)
    created_at: AwareDatetime
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)

    @model_validator(mode="after")
    def _lesson_requires_notes(self) -> RunFeedback:
        if self.verdict is FeedbackVerdict.FAILED_WITH_LESSON and not self.notes.strip():
            raise ValueError("failed-with-lesson feedback requires non-empty notes")
        return self


class CandidateStatus(StrEnum):
    """Lifecycle of a proposed corpus case."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"


class CorpusCandidate(BaseModel):
    """A proposed corpus case derived from a ``failed-with-lesson`` run.

    Inert until approved: it never affects certification while ``pending``, and a
    rejected candidate is archived and can never become a benchmark task.
    """

    model_config = ConfigDict(extra="forbid")

    candidate_id: str = Field(min_length=1)
    source_run_id: str = Field(min_length=1)
    workload_id: str = Field(min_length=1)
    input: dict[str, JsonValue] = Field(default_factory=dict)
    expected: ExpectedOutcome
    check: BenchmarkTaskCheck
    status: CandidateStatus = CandidateStatus.PENDING
    lesson: str = ""
    proposed_by: str = Field(min_length=1)
    created_at: AwareDatetime
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)

    def to_task(self) -> BenchmarkTask:
        """Return the benchmark task this candidate becomes once approved."""
        if self.status is not CandidateStatus.APPROVED:
            raise CandidateNotAllowedError(
                f"candidate {self.candidate_id!r} is {self.status.value}; only approved "
                "candidates become benchmark tasks"
            )
        return BenchmarkTask(
            id=f"candidate-{self.candidate_id}",
            name=f"learned: {self.workload_id}",
            input=dict(self.input),
            expected=self.expected,
            check=self.check,
        )


class CandidateReview(BaseModel):
    """A reviewer's decision on a corpus candidate (M36-03)."""

    model_config = ConfigDict(extra="forbid")

    review_id: str = Field(min_length=1)
    candidate_id: str = Field(min_length=1)
    reviewer: str = Field(min_length=1)
    decision: CandidateStatus
    reason: str | None = None
    reviewed_at: AwareDatetime
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)

    @model_validator(mode="after")
    def _decision_must_be_terminal(self) -> CandidateReview:
        if self.decision is CandidateStatus.PENDING:
            raise ValueError("a review decision must be approved or rejected")
        return self


class CorpusVersion(BaseModel):
    """An immutable cut of a corpus version and the task ids it contains (M36-04)."""

    model_config = ConfigDict(extra="forbid")

    corpus_version_id: str = Field(min_length=1)
    corpus_id: str = Field(min_length=1)
    version: int = Field(ge=1)
    task_ids: list[str] = Field(default_factory=list)
    created_at: AwareDatetime
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)


class RubricCriterion(BaseModel):
    """One criterion in a judge rubric."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    description: str = ""


class Rubric(BaseModel):
    """A versioned, immutable judge rubric (M36-05)."""

    model_config = ConfigDict(extra="forbid")

    rubric_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    version: int = Field(ge=1)
    criteria: list[RubricCriterion] = Field(min_length=1)
    created_at: AwareDatetime


class EvalSample(BaseModel):
    """A production run selected for online evaluation (M36-05)."""

    model_config = ConfigDict(extra="forbid")

    sample_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    workload_id: str = Field(min_length=1)
    rubric_version: int = Field(ge=1)
    sampled_at: AwareDatetime
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)


class JudgeCriterionScore(BaseModel):
    """A judge's score for one rubric criterion."""

    model_config = ConfigDict(extra="forbid")

    criterion: str = Field(min_length=1)
    score: float = Field(ge=0.0, le=1.0)


class JudgeResult(BaseModel):
    """The raw outcome of a judge invocation (before it is recorded)."""

    model_config = ConfigDict(extra="forbid")

    score: float = Field(ge=0.0, le=1.0)
    criteria: list[JudgeCriterionScore] = Field(default_factory=list)
    model_identity: str = Field(min_length=1)


class JudgeScore(BaseModel):
    """A recorded judge score for an eval sample (M36-06)."""

    model_config = ConfigDict(extra="forbid")

    score_id: str = Field(min_length=1)
    sample_id: str = Field(min_length=1)
    rubric_version: int = Field(ge=1)
    score: float = Field(ge=0.0, le=1.0)
    criteria: list[JudgeCriterionScore] = Field(default_factory=list)
    model_identity: str | None = None
    created_at: AwareDatetime
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)


class QualityScore(BaseModel):
    """A rolling-window production quality aggregate for a workload (M36-06)."""

    model_config = ConfigDict(extra="forbid")

    workload_id: str = Field(min_length=1)
    window: int = Field(ge=1)
    sample_count: int = Field(ge=0)
    mean_score: float = Field(ge=0.0, le=1.0)
    quality_target: float | None = None
    dip: bool = False
