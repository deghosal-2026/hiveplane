"""Tests for run feedback capture (M36-01)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError
from sqlalchemy import Engine

from hiveplane.config import Settings
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.execution.errors import RunNotFoundError
from hiveplane.learning.errors import FeedbackNotAllowedError, FeedbackNotFoundError
from hiveplane.learning.feedback import FeedbackService
from hiveplane.learning.models import FeedbackVerdict, RunFeedback
from hiveplane.learning.store import (
    InMemoryFeedbackStore,
    PostgresFeedbackStore,
    build_feedback_store,
)
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext
from postgres import ensure_schema

_NOW = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)
_OTHER = TenantContext(tenant_id="other")


def _run(
    run_id: str = "run-1",
    *,
    state: RunState = RunState.COMPLETED,
    workload: str = "repo-agent",
    tenant: str = "default",
) -> Run:
    return Run(
        id=run_id,
        workload_id=workload,
        caller="operator",
        state=state,
        context=AdmissionContext.PRODUCTION,
        task={"ticket": "T-1"},
        result={"ok": True},
        created_at=_NOW,
        updated_at=_NOW,
        finished_at=_NOW,
        tenant_id=tenant,
    )


class _Runs:
    """A minimal run reader backed by a dict (satisfies the service protocol)."""

    def __init__(self, runs: dict[str, Run]) -> None:
        self._runs = runs

    def get(self, run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT) -> Run:
        run = self._runs.get(run_id)
        if run is None:
            raise RunNotFoundError(run_id)
        return run


def _service(runs: dict[str, Run] | None = None) -> FeedbackService:
    counter = {"n": 0}

    def _id() -> str:
        counter["n"] += 1
        return f"fb-{counter['n']}"

    return FeedbackService(
        InMemoryFeedbackStore(),
        run_reader=_Runs(runs if runs is not None else {"run-1": _run()}),
        clock=lambda: _NOW,
        id_factory=_id,
    )


def test_verdict_values_are_canonical() -> None:
    assert FeedbackVerdict.GOOD.value == "good"
    assert FeedbackVerdict.BAD.value == "bad"
    assert FeedbackVerdict.FAILED_WITH_LESSON.value == "failed-with-lesson"


def test_invalid_verdict_is_rejected() -> None:
    with pytest.raises(ValidationError):
        RunFeedback.model_validate(
            {
                "feedback_id": "fb-1",
                "run_id": "run-1",
                "workload_id": "repo-agent",
                "verdict": "amazing",
                "notes": "",
                "operator": "alice",
                "created_at": _NOW,
            }
        )


def test_failed_with_lesson_requires_notes() -> None:
    with pytest.raises(ValidationError):
        RunFeedback.model_validate(
            {
                "feedback_id": "fb-1",
                "run_id": "run-1",
                "workload_id": "repo-agent",
                "verdict": "failed-with-lesson",
                "notes": "",
                "operator": "alice",
                "created_at": _NOW,
            }
        )


def test_record_feedback_for_a_terminal_run() -> None:
    service = _service()

    feedback = service.record(
        "run-1",
        verdict=FeedbackVerdict.FAILED_WITH_LESSON,
        notes="should have escalated the ticket",
        operator="alice",
    )

    assert feedback.feedback_id == "fb-1"
    assert feedback.run_id == "run-1"
    assert feedback.workload_id == "repo-agent"
    assert feedback.verdict is FeedbackVerdict.FAILED_WITH_LESSON
    assert feedback.operator == "alice"
    assert feedback.tenant_id == "default"
    assert service.get("fb-1").feedback_id == "fb-1"


def test_good_feedback_needs_no_notes() -> None:
    service = _service()

    feedback = service.record(
        "run-1", verdict=FeedbackVerdict.GOOD, operator="alice"
    )

    assert feedback.notes == ""
    assert feedback.verdict is FeedbackVerdict.GOOD


def test_feedback_requires_a_terminal_run() -> None:
    service = _service({"run-1": _run(state=RunState.RUNNING)})

    with pytest.raises(FeedbackNotAllowedError):
        service.record("run-1", verdict=FeedbackVerdict.GOOD, operator="alice")


def test_feedback_for_unknown_run_is_rejected() -> None:
    service = _service({})

    with pytest.raises(RunNotFoundError):
        service.record("ghost", verdict=FeedbackVerdict.GOOD, operator="alice")


def test_feedback_on_a_failed_run_is_allowed() -> None:
    service = _service({"run-1": _run(state=RunState.FAILED)})

    feedback = service.record(
        "run-1", verdict=FeedbackVerdict.BAD, operator="alice"
    )

    assert feedback.verdict is FeedbackVerdict.BAD


def test_list_feedback_filters_by_workload_and_run() -> None:
    service = _service(
        {
            "run-1": _run("run-1", workload="repo-agent"),
            "run-2": _run("run-2", workload="docs-agent"),
        }
    )
    service.record("run-1", verdict=FeedbackVerdict.GOOD, operator="alice")
    service.record("run-2", verdict=FeedbackVerdict.BAD, operator="bob")

    assert [f.run_id for f in service.list(workload="repo-agent")] == ["run-1"]
    assert [f.run_id for f in service.list(run_id="run-2")] == ["run-2"]
    assert len(service.list()) == 2


def test_feedback_is_tenant_scoped() -> None:
    service = _service({"run-1": _run(tenant="default")})
    service.record("run-1", verdict=FeedbackVerdict.GOOD, operator="alice")

    assert service.list(ctx=_OTHER) == []
    with pytest.raises(FeedbackNotFoundError):
        service.get("fb-1", ctx=_OTHER)


def test_get_unknown_feedback_raises() -> None:
    service = _service()

    with pytest.raises(FeedbackNotFoundError):
        service.get("ghost")


def test_builder_defaults_to_in_memory() -> None:
    assert isinstance(build_feedback_store(Settings()), InMemoryFeedbackStore)


def test_builder_selects_postgres() -> None:
    settings = Settings.model_validate({"execution": {"store": "postgres"}})
    assert isinstance(build_feedback_store(settings), PostgresFeedbackStore)


def test_postgres_feedback_round_trip(pg_engine: Engine) -> None:
    ensure_schema(pg_engine)
    store = PostgresFeedbackStore(pg_engine)
    store.clear()
    service = FeedbackService(
        store,
        run_reader=_Runs({"run-1": _run()}),
        clock=lambda: _NOW,
        id_factory=lambda: "fb-1",
    )
    service.record(
        "run-1",
        verdict=FeedbackVerdict.FAILED_WITH_LESSON,
        notes="lesson",
        operator="alice",
    )

    reopened = PostgresFeedbackStore(pg_engine)

    assert reopened.get_feedback("fb-1") is not None
    assert [f.feedback_id for f in reopened.list_feedback(workload="repo-agent")] == ["fb-1"]


def test_postgres_feedback_survives_new_engine(pg_engine: Engine) -> None:
    from hiveplane.persistence.base import create_engine_from_settings

    ensure_schema(pg_engine)
    store = PostgresFeedbackStore(pg_engine)
    store.clear()
    store.add_feedback(
        RunFeedback(
            feedback_id="fb-1",
            run_id="run-1",
            workload_id="repo-agent",
            verdict=FeedbackVerdict.GOOD,
            notes="",
            operator="alice",
            created_at=_NOW,
            tenant_id="default",
        )
    )

    fresh_engine = create_engine_from_settings()
    try:
        assert PostgresFeedbackStore(fresh_engine).get_feedback("fb-1") is not None
    finally:
        fresh_engine.dispose()


def test_failed_with_lesson_auto_proposes_a_candidate() -> None:
    from hiveplane.learning.candidate_store import InMemoryCandidateStore
    from hiveplane.learning.candidates import CandidateService

    candidate_service = CandidateService(
        InMemoryCandidateStore(), clock=lambda: _NOW, id_factory=lambda: "cand-1"
    )
    service = FeedbackService(
        InMemoryFeedbackStore(),
        run_reader=_Runs({"run-1": _run()}),
        clock=lambda: _NOW,
        id_factory=lambda: "fb-1",
        candidates=candidate_service,
    )

    service.record(
        "run-1",
        verdict=FeedbackVerdict.FAILED_WITH_LESSON,
        notes="escalate",
        operator="alice",
    )

    candidate = candidate_service.get("cand-1")
    assert candidate.source_run_id == "run-1"
    assert candidate.expected.outcome == "escalate"


def test_good_feedback_does_not_propose_a_candidate() -> None:
    from hiveplane.learning.candidate_store import InMemoryCandidateStore
    from hiveplane.learning.candidates import CandidateService

    candidate_service = CandidateService(InMemoryCandidateStore(), clock=lambda: _NOW)
    service = FeedbackService(
        InMemoryFeedbackStore(),
        run_reader=_Runs({"run-1": _run()}),
        clock=lambda: _NOW,
        candidates=candidate_service,
    )

    service.record("run-1", verdict=FeedbackVerdict.GOOD, operator="alice")

    assert candidate_service.list() == []
