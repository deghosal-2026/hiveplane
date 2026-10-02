"""The learning loop: production feedback, corpus candidates, and online eval (M36)."""

from __future__ import annotations

from hiveplane.learning.candidate_store import (
    CandidateStore,
    InMemoryCandidateStore,
    PostgresCandidateStore,
    build_candidate_store,
)
from hiveplane.learning.candidates import CandidateService
from hiveplane.learning.corpus_store import (
    CorpusVersionStore,
    InMemoryCorpusVersionStore,
    PostgresCorpusVersionStore,
    build_corpus_version_store,
)
from hiveplane.learning.corpus_versions import CorpusVersionService
from hiveplane.learning.errors import (
    CandidateAlreadyExistsError,
    CandidateAlreadyReviewedError,
    CandidateNotAllowedError,
    CandidateNotFoundError,
    FeedbackNotAllowedError,
    FeedbackNotFoundError,
    LearningError,
    RubricNotFoundError,
)
from hiveplane.learning.eval import EvalService
from hiveplane.learning.eval_store import (
    EvalStore,
    InMemoryEvalStore,
    PostgresEvalStore,
    build_eval_store,
)
from hiveplane.learning.feedback import FeedbackService, RunReader
from hiveplane.learning.judge import RubricJudge
from hiveplane.learning.models import (
    CandidateReview,
    CandidateStatus,
    CorpusCandidate,
    CorpusVersion,
    EvalSample,
    FeedbackVerdict,
    JudgeCriterionScore,
    JudgeResult,
    JudgeScore,
    QualityScore,
    Rubric,
    RubricCriterion,
    RunFeedback,
)
from hiveplane.learning.rubrics import RubricRegistry, default_rubric
from hiveplane.learning.sampling import is_pii, sample_bucket, should_sample
from hiveplane.learning.store import (
    FeedbackStore,
    InMemoryFeedbackStore,
    PostgresFeedbackStore,
    build_feedback_store,
)

__all__ = [
    "CandidateAlreadyExistsError",
    "CandidateAlreadyReviewedError",
    "CandidateNotAllowedError",
    "CandidateNotFoundError",
    "CandidateReview",
    "CandidateService",
    "CandidateStatus",
    "CandidateStore",
    "CorpusCandidate",
    "CorpusVersion",
    "CorpusVersionService",
    "CorpusVersionStore",
    "EvalSample",
    "EvalService",
    "EvalStore",
    "FeedbackNotAllowedError",
    "FeedbackNotFoundError",
    "FeedbackService",
    "FeedbackStore",
    "FeedbackVerdict",
    "InMemoryCandidateStore",
    "InMemoryCorpusVersionStore",
    "InMemoryEvalStore",
    "InMemoryFeedbackStore",
    "JudgeCriterionScore",
    "JudgeResult",
    "JudgeScore",
    "LearningError",
    "PostgresCandidateStore",
    "PostgresCorpusVersionStore",
    "PostgresEvalStore",
    "PostgresFeedbackStore",
    "QualityScore",
    "Rubric",
    "RubricCriterion",
    "RubricJudge",
    "RubricNotFoundError",
    "RubricRegistry",
    "RunFeedback",
    "RunReader",
    "build_candidate_store",
    "build_corpus_version_store",
    "build_eval_store",
    "build_feedback_store",
    "default_rubric",
    "is_pii",
    "sample_bucket",
    "should_sample",
]
