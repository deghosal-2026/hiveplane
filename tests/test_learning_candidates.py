"""Tests for feedback → corpus candidates and the review gate (M36-02, M36-03)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine

from hiveplane.certification.models import BenchmarkTask, CheckType
from hiveplane.config import Settings
from hiveplane.learning.candidate_store import (
    InMemoryCandidateStore,
    PostgresCandidateStore,
    build_candidate_store,
)
from hiveplane.learning.candidates import CandidateService
from hiveplane.learning.errors import (
    CandidateAlreadyExistsError,
    CandidateAlreadyReviewedError,
    CandidateNotAllowedError,
    CandidateNotFoundError,
)
from hiveplane.learning.models import (
    CandidateReview,
    CandidateStatus,
    CorpusCandidate,
    FeedbackVerdict,
    RunFeedback,
)
from hiveplane.tenancy import TenantContext
from postgres import ensure_schema
from test_learning_feedback import _run

_NOW = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)
_OTHER = TenantContext(tenant_id="other")


def _feedback(
    *,
    verdict: FeedbackVerdict = FeedbackVerdict.FAILED_WITH_LESSON,
    notes: str = "should have escalated the ticket",
    run_id: str = "run-1",
    tenant: str = "default",
) -> RunFeedback:
    return RunFeedback(
        feedback_id="fb-1",
        run_id=run_id,
        workload_id="repo-agent",
        verdict=verdict,
        notes=notes,
        operator="alice",
        created_at=_NOW,
        tenant_id=tenant,
    )


def _service() -> CandidateService:
    counter = {"n": 0}

    def _id() -> str:
        counter["n"] += 1
        return f"cand-{counter['n']}"

    return CandidateService(
        InMemoryCandidateStore(),
        clock=lambda: _NOW,
        id_factory=_id,
        review_id_factory=lambda: f"rev-{counter['n']}",
    )


def test_propose_from_failed_with_lesson() -> None:
    service = _service()

    candidate = service.propose(_feedback(), _run())

    assert candidate.candidate_id == "cand-1"
    assert candidate.source_run_id == "run-1"
    assert candidate.workload_id == "repo-agent"
    assert candidate.status is CandidateStatus.PENDING
    assert candidate.input == {"ticket": "T-1"}
    assert candidate.expected.outcome == "should have escalated the ticket"
    assert candidate.check.type is CheckType.EXACT_MATCH
    assert candidate.check.field == "output"
    assert candidate.check.value == "should have escalated the ticket"
    assert candidate.proposed_by == "alice"
    assert service.get("cand-1").candidate_id == "cand-1"


def test_propose_uses_a_judge_hint_when_given() -> None:
    service = _service()

    candidate = service.propose(_feedback(), _run(), judge_hint="escalate")

    assert candidate.expected.outcome == "escalate"
    assert candidate.check.value == "escalate"


def test_propose_requires_failed_with_lesson() -> None:
    service = _service()

    with pytest.raises(CandidateNotAllowedError):
        service.propose(_feedback(verdict=FeedbackVerdict.GOOD, notes=""), _run())


def test_propose_is_not_duplicated_for_the_same_feedback() -> None:
    service = _service()
    service.propose(_feedback(), _run())

    with pytest.raises(CandidateAlreadyExistsError):
        service.propose(_feedback(), _run())


def test_list_candidates_filters_by_workload_and_status() -> None:
    service = _service()
    service.propose(_feedback(), _run("run-1", workload="repo-agent"))

    assert [c.source_run_id for c in service.list(workload="repo-agent")] == ["run-1"]
    assert service.list(workload="docs-agent") == []
    assert service.list(status=CandidateStatus.APPROVED) == []


def test_get_unknown_candidate_raises() -> None:
    service = _service()

    with pytest.raises(CandidateNotFoundError):
        service.get("ghost")


def test_approval_promotes_a_candidate_and_records_a_review() -> None:
    service = _service()
    service.propose(_feedback(), _run())

    candidate = service.approve("cand-1", reviewer="bob")

    assert candidate.status is CandidateStatus.APPROVED
    review = service.reviews("cand-1")[0]
    assert review.decision is CandidateStatus.APPROVED
    assert review.reviewer == "bob"
    assert review.candidate_id == "cand-1"


def test_rejection_archives_the_candidate_with_a_reason() -> None:
    service = _service()
    service.propose(_feedback(), _run())

    candidate = service.reject("cand-1", reviewer="bob", reason="expectation is wrong")

    assert candidate.status is CandidateStatus.REJECTED
    review = service.reviews("cand-1")[0]
    assert review.decision is CandidateStatus.REJECTED
    assert review.reason == "expectation is wrong"


def test_candidate_cannot_be_reviewed_twice() -> None:
    service = _service()
    service.propose(_feedback(), _run())
    service.approve("cand-1", reviewer="bob")

    with pytest.raises(CandidateAlreadyReviewedError):
        service.reject("cand-1", reviewer="bob", reason="late")


def test_approved_candidate_becomes_a_benchmark_task() -> None:
    service = _service()
    service.propose(_feedback(), _run())
    candidate = service.approve("cand-1", reviewer="bob")

    task = candidate.to_task()

    assert isinstance(task, BenchmarkTask)
    assert task.id == "candidate-cand-1"
    assert task.input == {"ticket": "T-1"}
    assert task.check.value == "should have escalated the ticket"


def test_rejected_candidate_cannot_become_a_task() -> None:
    service = _service()
    service.propose(_feedback(), _run())
    candidate = service.reject("cand-1", reviewer="bob", reason="wrong")

    with pytest.raises(CandidateNotAllowedError):
        candidate.to_task()


def test_candidates_are_tenant_scoped() -> None:
    service = _service()
    service.propose(_feedback(), _run())

    assert service.list(ctx=_OTHER) == []
    with pytest.raises(CandidateNotFoundError):
        service.get("cand-1", ctx=_OTHER)


def test_builder_defaults_to_in_memory() -> None:
    assert isinstance(build_candidate_store(Settings()), InMemoryCandidateStore)


def test_builder_selects_postgres() -> None:
    settings = Settings.model_validate({"execution": {"store": "postgres"}})
    assert isinstance(build_candidate_store(settings), PostgresCandidateStore)


def test_postgres_candidate_round_trip(pg_engine: Engine) -> None:
    ensure_schema(pg_engine)
    store = PostgresCandidateStore(pg_engine)
    store.clear()
    service = CandidateService(
        store, clock=lambda: _NOW, id_factory=lambda: "cand-1"
    )
    service.propose(_feedback(), _run())
    service.approve("cand-1", reviewer="bob")

    reopened = CandidateService(PostgresCandidateStore(pg_engine))

    candidate = reopened.get("cand-1")
    assert candidate.status is CandidateStatus.APPROVED
    assert reopened.reviews("cand-1")[0].reviewer == "bob"


def test_candidate_review_model_requires_a_decision() -> None:
    with pytest.raises(ValidationError):
        CandidateReview.model_validate(
            {
                "review_id": "rev-1",
                "candidate_id": "cand-1",
                "reviewer": "bob",
                "decision": "pending",
                "reviewed_at": _NOW,
            }
        )


def test_corpus_candidate_defaults_to_pending() -> None:
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

    assert candidate.status is CandidateStatus.PENDING
