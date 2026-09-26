"""Tests for corpus versioning integration (M36-04)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import Engine

from hiveplane.certification.models import (
    BenchmarkCorpus,
    BenchmarkTask,
    BenchmarkTaskCheck,
    CheckType,
    ExpectedOutcome,
)
from hiveplane.config import Settings
from hiveplane.learning.candidate_store import InMemoryCandidateStore
from hiveplane.learning.candidates import CandidateService
from hiveplane.learning.corpus_store import (
    InMemoryCorpusVersionStore,
    PostgresCorpusVersionStore,
    build_corpus_version_store,
)
from hiveplane.learning.corpus_versions import CorpusVersionService
from hiveplane.learning.models import (
    CandidateStatus,
    CorpusVersion,
    FeedbackVerdict,
    RunFeedback,
)
from postgres import ensure_schema
from test_learning_feedback import _run

_NOW = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)


def _base_corpus() -> BenchmarkCorpus:
    return BenchmarkCorpus(
        id="repo-agent-corpus",
        version=2,
        tasks=[
            BenchmarkTask(
                id="t1",
                name="classify",
                input={"ticket": "T-0"},
                expected=ExpectedOutcome(outcome="low"),
                check=BenchmarkTaskCheck(type=CheckType.EXACT_MATCH, field="risk", value="low"),
            )
        ],
    )


def _feedback() -> RunFeedback:
    return RunFeedback(
        feedback_id="fb-1",
        run_id="run-1",
        workload_id="repo-agent",
        verdict=FeedbackVerdict.FAILED_WITH_LESSON,
        notes="should escalate",
        operator="alice",
        created_at=_NOW,
    )


def _service() -> tuple[CorpusVersionService, CandidateService]:
    counter = {"n": 0}

    def _id() -> str:
        counter["n"] += 1
        return f"cand-{counter['n']}"

    candidates = CandidateService(
        InMemoryCandidateStore(), clock=lambda: _NOW, id_factory=_id
    )
    service = CorpusVersionService(
        InMemoryCorpusVersionStore(),
        candidates=candidates,
        clock=lambda: _NOW,
        id_factory=lambda: "cv-1",
    )
    return service, candidates


def test_integrate_without_candidates_returns_base() -> None:
    service, _ = _service()

    result = service.integrate("repo-agent", _base_corpus())

    assert result.version == 2
    assert [task.id for task in result.tasks] == ["t1"]


def test_integrate_appends_approved_candidates_and_bumps_version() -> None:
    service, candidates = _service()
    candidates.propose(_feedback(), _run())
    candidates.approve("cand-1", reviewer="bob")
    service.integrate("repo-agent", _base_corpus())
    candidates.propose(
        RunFeedback(
            feedback_id="fb-2",
            run_id="run-2",
            workload_id="repo-agent",
            verdict=FeedbackVerdict.FAILED_WITH_LESSON,
            notes="should escalate",
            operator="alice",
            created_at=_NOW,
        ),
        _run("run-2"),
    )

    result = service.integrate("repo-agent", _base_corpus())

    assert result.version == 3
    assert [task.id for task in result.tasks] == ["t1", "candidate-cand-1"]
    assert result.tasks[1].check.value == "should escalate"


def test_rejected_candidates_are_never_integrated() -> None:
    service, candidates = _service()
    candidates.propose(_feedback(), _run())
    candidates.reject("cand-1", reviewer="bob", reason="wrong")

    result = service.integrate("repo-agent", _base_corpus())

    assert result.version == 2
    assert [task.id for task in result.tasks] == ["t1"]


def test_integrate_is_idempotent() -> None:
    service, candidates = _service()
    candidates.propose(_feedback(), _run())
    candidates.approve("cand-1", reviewer="bob")

    first = service.integrate("repo-agent", _base_corpus())
    second = service.integrate("repo-agent", _base_corpus())

    assert first == second


def test_cut_records_a_corpus_version() -> None:
    service, candidates = _service()
    candidates.propose(_feedback(), _run())
    candidates.approve("cand-1", reviewer="bob")

    service.integrate("repo-agent", _base_corpus())

    versions = service.versions("repo-agent-corpus")
    assert len(versions) == 1
    assert versions[0].version == 3
    assert versions[0].task_ids == ["t1", "candidate-cand-1"]


def test_builder_defaults_to_in_memory() -> None:
    assert isinstance(build_corpus_version_store(Settings()), InMemoryCorpusVersionStore)


def test_builder_selects_postgres() -> None:
    settings = Settings.model_validate({"execution": {"store": "postgres"}})
    assert isinstance(build_corpus_version_store(settings), PostgresCorpusVersionStore)


def test_postgres_corpus_version_round_trip(pg_engine: Engine) -> None:
    ensure_schema(pg_engine)
    store = PostgresCorpusVersionStore(pg_engine)
    store.clear()
    candidates = CandidateService(
        InMemoryCandidateStore(), clock=lambda: _NOW, id_factory=lambda: "cand-1"
    )
    candidates.propose(_feedback(), _run())
    candidates.approve("cand-1", reviewer="bob")
    service = CorpusVersionService(
        store, candidates=candidates, clock=lambda: _NOW, id_factory=lambda: "cv-1"
    )
    service.integrate("repo-agent", _base_corpus())

    reopened = CorpusVersionService(PostgresCorpusVersionStore(pg_engine), candidates=candidates)

    assert reopened.versions("repo-agent-corpus")[0].task_ids == [
        "t1",
        "candidate-cand-1",
    ]


def test_corpus_version_model_requires_task_ids() -> None:
    version = CorpusVersion(
        corpus_version_id="cv-1",
        corpus_id="c",
        version=3,
        task_ids=["t1"],
        created_at=_NOW,
        tenant_id="default",
    )
    assert version.task_ids == ["t1"]
    assert CandidateStatus.APPROVED.value == "approved"


def test_certification_includes_approved_candidates(make_manifest: Any) -> None:
    """The next certification executes the integrated corpus (M36-04 acceptance)."""
    import tempfile
    from pathlib import Path

    from hiveplane.certification.runner import ReferenceExecutor
    from hiveplane.certification.store import InMemoryCertificationStore
    from hiveplane.certification.workflow import CertificationCoordinator
    from test_certification_workflow import (
        _ENV,
        _FIXED_NOW,
        _TASKS,
        _setup,
        _write_corpus,
    )

    with tempfile.TemporaryDirectory() as tmp:
        corpora_dir = _write_corpus(Path(tmp), _TASKS)
        registry, service, _ = _setup(make_manifest, corpora_dir)
        candidates = CandidateService(
            InMemoryCandidateStore(), clock=lambda: _NOW, id_factory=lambda: "cand-1"
        )
        candidates.propose(_feedback(), _run())
        candidates.approve("cand-1", reviewer="bob")
        corpus_service = CorpusVersionService(
            InMemoryCorpusVersionStore(),
            candidates=candidates,
            clock=lambda: _NOW,
            id_factory=lambda: "cv-1",
        )
        coordinator = CertificationCoordinator(
            registry,
            service,
            InMemoryCertificationStore(),
            executor=ReferenceExecutor(),
            corpora_dir=corpora_dir,
            environment=_ENV,
            clock=lambda: _FIXED_NOW,
            corpus_integrator=lambda workload, corpus: corpus_service.integrate(
                workload, corpus
            ),
        )

        result = coordinator.benchmark("repo-agent")

    assert result.aggregate.total == len(_TASKS) + 1
    assert result.corpus_version == 2
