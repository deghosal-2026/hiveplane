"""Edge-path tests for the learning stores, rubrics, and judge (M36 coverage)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine

from hiveplane.learning.candidate_store import (
    InMemoryCandidateStore,
    PostgresCandidateStore,
)
from hiveplane.learning.corpus_store import (
    InMemoryCorpusVersionStore,
    PostgresCorpusVersionStore,
)
from hiveplane.learning.errors import RubricNotFoundError
from hiveplane.learning.eval_store import InMemoryEvalStore, PostgresEvalStore
from hiveplane.learning.judge import RubricJudge
from hiveplane.learning.models import (
    CandidateStatus,
    CorpusVersion,
    EvalSample,
    FeedbackVerdict,
    JudgeScore,
    Rubric,
    RubricCriterion,
    RunFeedback,
)
from hiveplane.learning.rubrics import RubricRegistry
from hiveplane.learning.store import InMemoryFeedbackStore, PostgresFeedbackStore
from hiveplane.tenancy import TenantContext
from postgres import ensure_schema
from test_learning_eval import _FakeProvider
from test_learning_feedback import _run

_NOW = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)
_OTHER = TenantContext(tenant_id="other")


def _feedback(feedback_id: str = "fb-1") -> RunFeedback:
    return RunFeedback(
        feedback_id=feedback_id,
        run_id="run-1",
        workload_id="repo-agent",
        verdict=FeedbackVerdict.GOOD,
        notes="",
        operator="alice",
        created_at=_NOW,
    )


def _sample(sample_id: str = "sample-1") -> EvalSample:
    return EvalSample(
        sample_id=sample_id,
        run_id="run-1",
        workload_id="repo-agent",
        rubric_version=1,
        sampled_at=_NOW,
    )


def _score(score_id: str = "score-1") -> JudgeScore:
    return JudgeScore(
        score_id=score_id,
        sample_id="sample-1",
        rubric_version=1,
        score=0.5,
        created_at=_NOW,
    )


def test_feedback_store_edges() -> None:
    store = InMemoryFeedbackStore()
    store.add_feedback(_feedback())

    assert store.get_feedback("ghost") is None
    assert store.get_feedback("fb-1", ctx=_OTHER) is None
    assert store.list_feedback(verdict=FeedbackVerdict.BAD) == []
    assert store.list_feedback(ctx=_OTHER) == []


def test_postgres_feedback_store_edges(pg_engine: Engine) -> None:
    ensure_schema(pg_engine)
    store = PostgresFeedbackStore(pg_engine)
    store.clear()
    store.add_feedback(_feedback())
    store.add_feedback(_feedback())  # exercise the update branch

    assert store.get_feedback("ghost") is None
    assert store.list_feedback(verdict=FeedbackVerdict.BAD) == []
    store.clear()


def test_candidate_store_edges() -> None:
    store = InMemoryCandidateStore()
    assert store.get_candidate("ghost") is None
    assert store.list_reviews("ghost") == []
    assert store.list_candidates(ctx=_OTHER) == []


def test_postgres_candidate_store_edges(pg_engine: Engine) -> None:
    from hiveplane.learning.models import CorpusCandidate

    ensure_schema(pg_engine)
    store = PostgresCandidateStore(pg_engine)
    store.clear()
    candidate = CorpusCandidate.model_validate(
        {
            "candidate_id": "cand-1",
            "source_run_id": "run-1",
            "workload_id": "repo-agent",
            "input": {},
            "expected": {"outcome": "x"},
            "check": {"type": "exact_match", "field": "output", "value": "x"},
            "proposed_by": "alice",
            "created_at": _NOW,
        }
    )
    store.add_candidate(candidate)
    store.add_candidate(candidate.model_copy(update={"status": CandidateStatus.APPROVED}))

    assert store.get_candidate("ghost") is None
    assert [c.candidate_id for c in store.list_candidates(status=CandidateStatus.APPROVED)] == [
        "cand-1"
    ]
    assert store.list_reviews("cand-1") == []
    store.clear()


def test_corpus_version_store_edges() -> None:
    store = InMemoryCorpusVersionStore()
    assert store.get_version("corpus", 1) is None
    assert store.list_versions("corpus") == []


def test_postgres_corpus_version_store_edges(pg_engine: Engine) -> None:
    ensure_schema(pg_engine)
    store = PostgresCorpusVersionStore(pg_engine)
    store.clear()
    version = CorpusVersion(
        corpus_version_id="cv-1",
        corpus_id="corpus",
        version=2,
        task_ids=["t1"],
        created_at=_NOW,
    )
    store.add_version(version)
    store.add_version(version)  # exercise the update branch

    assert store.get_version("corpus", 99) is None
    assert store.list_versions("corpus")[0].task_ids == ["t1"]
    store.clear()


def test_eval_store_edges() -> None:
    store = InMemoryEvalStore()
    store.add_sample(_sample())
    store.add_score(_score())

    assert store.get_sample("ghost") is None
    assert store.list_samples(run_id="other") == []
    assert store.list_scores(sample_id="other") == []
    assert store.list_rubrics("default") == []
    assert store.get_rubric("default", 1) is None


def test_postgres_eval_store_edges(pg_engine: Engine) -> None:
    ensure_schema(pg_engine)
    store = PostgresEvalStore(pg_engine)
    store.clear()
    store.add_sample(_sample())
    store.add_sample(_sample())  # update branch
    store.add_score(_score())
    rubric = Rubric(
        rubric_id="default-v1",
        name="default",
        version=1,
        criteria=[RubricCriterion(name="accuracy")],
        created_at=_NOW,
    )
    store.add_rubric(rubric)
    store.add_rubric(rubric)  # idempotent insert

    assert store.get_sample("ghost") is None
    assert store.list_scores(sample_id="score-x") == []
    assert store.list_scores(workload="repo-agent")[0].score_id == "score-1"
    assert store.get_rubric("default", 1) is not None
    assert store.get_rubric("default", 99) is None
    assert store.list_rubrics("default")[0].rubric_id == "default-v1"
    store.clear()


def test_rubric_registry_missing_versions_raise() -> None:
    registry = RubricRegistry(InMemoryEvalStore(), clock=lambda: _NOW)

    with pytest.raises(RubricNotFoundError):
        registry.get("default", 1)
    with pytest.raises(RubricNotFoundError):
        registry.latest("default")


def test_rubric_registry_latest_returns_newest() -> None:
    registry = RubricRegistry(InMemoryEvalStore(), clock=lambda: _NOW)
    registry.publish("default", [RubricCriterion(name="accuracy")])
    second = registry.publish("default", [RubricCriterion(name="safety")])

    assert registry.latest("default") == second


def test_judge_handles_invalid_json_object() -> None:
    provider = _FakeProvider("{not valid json}")
    judge = RubricJudge(provider, model="judge-model", clock=lambda: _NOW)

    result = judge.judge(_run(), RubricRegistry(InMemoryEvalStore()).publish(
        "default", [RubricCriterion(name="accuracy")]
    ))

    assert result.score == 0.0
    assert result.criteria == []


def test_in_memory_corpus_store_upsert_and_clear() -> None:
    store = InMemoryCorpusVersionStore()
    version = CorpusVersion(
        corpus_version_id="cv-1",
        corpus_id="corpus",
        version=2,
        task_ids=["t1"],
        created_at=_NOW,
    )
    store.add_version(version)
    store.add_version(
        version.model_copy(update={"corpus_version_id": "cv-2", "task_ids": ["t1", "t2"]})
    )

    assert store.get_version("corpus", 2).task_ids == ["t1", "t2"]  # type: ignore[union-attr]
    store.clear()
    assert store.list_versions("corpus") == []


def test_feedback_store_clear() -> None:
    store = InMemoryFeedbackStore()
    store.add_feedback(_feedback())
    store.clear()

    assert store.list_feedback() == []


def test_judge_handles_non_numeric_scores() -> None:
    provider = _FakeProvider('{"score": "abc", "criteria": [{"criterion": "x", "score": null}]}')
    judge = RubricJudge(provider, model="judge-model", clock=lambda: _NOW)

    result = judge.judge(
        _run(),
        RubricRegistry(InMemoryEvalStore()).publish(
            "default", [RubricCriterion(name="accuracy")]
        ),
    )

    assert result.score == 0.0
    assert result.criteria[0].score == 0.0


def test_maybe_sample_with_explicit_pii_flag() -> None:
    from hiveplane.learning.eval import EvalService
    from hiveplane.learning.rubrics import default_rubric

    service = EvalService(
        InMemoryEvalStore(),
        judge=RubricJudge(_FakeProvider("{}"), model="judge-model"),
        rubrics=RubricRegistry(InMemoryEvalStore()),
    )

    assert (
        service.maybe_sample(_run(), sample_rate=100, rubric=default_rubric(), pii=True)
        is None
    )
