"""Tests for online eval sampling, the LLM judge, and the quality signal
(M36-05, M36-06, M36-07)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine

from hiveplane.config import Settings
from hiveplane.core.run import Run, RunState
from hiveplane.learning.eval import EvalService
from hiveplane.learning.eval_store import (
    InMemoryEvalStore,
    PostgresEvalStore,
    build_eval_store,
)
from hiveplane.learning.judge import RubricJudge
from hiveplane.learning.models import (
    EvalSample,
    JudgeScore,
    QualityScore,
    Rubric,
    RubricCriterion,
)
from hiveplane.learning.rubrics import RubricRegistry, default_rubric
from hiveplane.learning.sampling import is_pii, sample_bucket, should_sample
from hiveplane.llm.models import CompletionRequest, CompletionResponse, TokenUsage
from postgres import ensure_schema
from test_learning_feedback import _run

_NOW = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)


class _FakeProvider:
    """Returns a fixed judge JSON payload; records the requests it saw."""

    def __init__(self, content: str) -> None:
        self.content = content
        self.requests: list[CompletionRequest] = []

    def complete(self, request: CompletionRequest) -> CompletionResponse:
        self.requests.append(request)
        return CompletionResponse(
            content=self.content,
            model_identity="judge-model",
            usage=TokenUsage(input_tokens=10, output_tokens=5),
        )


def _rubric() -> Rubric:
    return default_rubric()


def _judge(content: str | None = None) -> RubricJudge:
    payload = content or (
        '{"score": 0.8, "criteria": [{"criterion": "accuracy", "score": 0.9}, '
        '{"criterion": "completeness", "score": 0.7}]}'
    )
    return RubricJudge(_FakeProvider(payload), model="judge-model", clock=lambda: _NOW)


def _require(sample: EvalSample | None) -> EvalSample:
    assert sample is not None
    return sample


def _service(*, judge: RubricJudge | None = None) -> EvalService:
    return EvalService(
        InMemoryEvalStore(),
        judge=judge or _judge(),
        rubrics=RubricRegistry(InMemoryEvalStore(), clock=lambda: _NOW),
        clock=lambda: _NOW,
        id_factory=lambda: "sample-1",
        score_id_factory=lambda: "score-1",
    )


# --------------------------------------------------------------------------- #
# Sampling (M36-05, M36-07)
# --------------------------------------------------------------------------- #
def test_sampling_is_deterministic() -> None:
    assert should_sample("run-1", 100) is True
    assert should_sample("run-1", 0) is False
    assert should_sample("run-1", 50) == should_sample("run-1", 50)
    assert sample_bucket("run-1") == sample_bucket("run-1")


def test_sampling_respects_the_configured_rate() -> None:
    selected = sum(should_sample(f"run-{i}", 20) for i in range(1000))

    assert 120 <= selected <= 280


def test_pii_detection_matches_sensitive_inputs() -> None:
    run = Run(
        id="run-1",
        workload_id="repo-agent",
        caller="op",
        state=RunState.COMPLETED,
        task={"email": "alice@example.com"},
        created_at=_NOW,
        updated_at=_NOW,
        finished_at=_NOW,
    )

    assert is_pii(run, ("email", "@")) is True


def test_sampling_skips_pii_runs() -> None:
    run = _run()
    run = run.model_copy(update={"task": {"ssn": "123-45-6789"}})
    service = _service()

    assert (
        service.maybe_sample(
            run, sample_rate=100, rubric=_rubric(), pii_patterns=("ssn",)
        )
        is None
    )


def test_sampling_skips_when_the_cost_cap_is_reached() -> None:
    service = _service()

    assert (
        service.maybe_sample(
            _run(),
            sample_rate=100,
            rubric=_rubric(),
            cost_cap_usd=1.0,
            spent_usd=1.0,
        )
        is None
    )


def test_sampling_creates_a_sample_when_selected() -> None:
    service = _service()

    sample = service.maybe_sample(_run(), sample_rate=100, rubric=_rubric())

    assert sample is not None
    assert sample.run_id == "run-1"
    assert sample.workload_id == "repo-agent"
    assert sample.rubric_version == _rubric().version
    assert service.samples(workload="repo-agent")[0].sample_id == "sample-1"


# --------------------------------------------------------------------------- #
# Judge (M36-05)
# --------------------------------------------------------------------------- #
def test_judge_scores_a_run_against_a_rubric() -> None:
    service = _service()
    sample = service.maybe_sample(_run(), sample_rate=100, rubric=_rubric())

    score = service.score(_require(sample), _run(), rubric=_rubric())

    assert isinstance(score, JudgeScore)
    assert score.sample_id == "sample-1"
    assert score.score == pytest.approx(0.8)
    assert {c.criterion for c in score.criteria} == {"accuracy", "completeness"}
    assert score.model_identity == "judge-model"
    assert service.scores(workload="repo-agent")[0].score == pytest.approx(0.8)


def test_judge_handles_malformed_json() -> None:
    service = _service(judge=_judge("not json"))
    sample = service.maybe_sample(_run(), sample_rate=100, rubric=_rubric())

    score = service.score(_require(sample), _run(), rubric=_rubric())

    assert score.score == 0.0
    assert score.criteria == []


def test_judge_score_bounds_scores() -> None:
    service = _service(judge=_judge('{"score": 5, "criteria": []}'))
    sample = service.maybe_sample(_run(), sample_rate=100, rubric=_rubric())

    score = service.score(_require(sample), _run(), rubric=_rubric())

    assert score.score == 1.0


# --------------------------------------------------------------------------- #
# Quality signal (M36-06)
# --------------------------------------------------------------------------- #
def test_quality_aggregates_scores_and_flags_a_dip() -> None:
    service = _service(judge=_judge('{"score": 0.4, "criteria": []}'))
    sample = service.maybe_sample(_run(), sample_rate=100, rubric=_rubric())
    service.score(_require(sample), _run(), rubric=_rubric())

    quality = service.quality("repo-agent", quality_target=0.8)

    assert isinstance(quality, QualityScore)
    assert quality.sample_count == 1
    assert quality.mean_score == pytest.approx(0.4)
    assert quality.dip is True
    assert quality.quality_target == pytest.approx(0.8)


def test_quality_is_ok_when_above_target() -> None:
    service = _service(judge=_judge('{"score": 0.95, "criteria": []}'))
    sample = service.maybe_sample(_run(), sample_rate=100, rubric=_rubric())
    service.score(_require(sample), _run(), rubric=_rubric())

    quality = service.quality("repo-agent", quality_target=0.8)

    assert quality.dip is False


def test_quality_with_no_samples_is_unknown() -> None:
    service = _service()

    quality = service.quality("repo-agent", quality_target=0.8)

    assert quality.sample_count == 0
    assert quality.dip is False


# --------------------------------------------------------------------------- #
# Rubrics (M36-05)
# --------------------------------------------------------------------------- #
def test_default_rubric_has_criteria() -> None:
    rubric = default_rubric()

    assert rubric.version == 1
    assert {criterion.name for criterion in rubric.criteria} >= {"accuracy"}


def test_rubric_registry_pins_immutable_versions() -> None:
    registry = RubricRegistry(InMemoryEvalStore(), clock=lambda: _NOW)

    first = registry.publish("default", [RubricCriterion(name="accuracy", description="x")])
    second = registry.publish("default", [RubricCriterion(name="accuracy", description="y")])

    assert first.version == 1
    assert second.version == 2
    assert registry.get("default", 1) == first
    assert registry.get("default", 1).criteria[0].description == "x"


# --------------------------------------------------------------------------- #
# Store (M36-05/06)
# --------------------------------------------------------------------------- #
def test_builder_defaults_to_in_memory() -> None:
    assert isinstance(build_eval_store(Settings()), InMemoryEvalStore)


def test_builder_selects_postgres() -> None:
    settings = Settings.model_validate({"execution": {"store": "postgres"}})
    assert isinstance(build_eval_store(settings), PostgresEvalStore)


def test_postgres_eval_round_trip(pg_engine: Engine) -> None:
    ensure_schema(pg_engine)
    store = PostgresEvalStore(pg_engine)
    store.clear()
    service = EvalService(
        store,
        judge=_judge(),
        rubrics=RubricRegistry(store, clock=lambda: _NOW),
        clock=lambda: _NOW,
        id_factory=lambda: "sample-1",
        score_id_factory=lambda: "score-1",
    )
    sample = service.maybe_sample(_run(), sample_rate=100, rubric=_rubric())
    service.score(_require(sample), _run(), rubric=_rubric())

    reopened = PostgresEvalStore(pg_engine)

    assert reopened.get_sample("sample-1") is not None
    assert reopened.list_scores(workload="repo-agent")[0].score == pytest.approx(0.8)


def test_run_without_expected_task_is_still_scorable() -> None:
    service = _service(judge=_judge('{"score": 0.5, "criteria": []}'))
    run = _run().model_copy(update={"result": None})
    sample = service.maybe_sample(run, sample_rate=100, rubric=_rubric())

    score = service.score(_require(sample), run, rubric=_rubric())

    assert score.score == pytest.approx(0.5)


def test_judge_request_includes_the_rubric_and_run() -> None:
    provider = _FakeProvider('{"score": 0.5, "criteria": []}')
    judge = RubricJudge(provider, model="judge-model", clock=lambda: _NOW)
    service = EvalService(
        InMemoryEvalStore(),
        judge=judge,
        rubrics=RubricRegistry(InMemoryEvalStore(), clock=lambda: _NOW),
        clock=lambda: _NOW,
    )
    sample = service.maybe_sample(_run(), sample_rate=100, rubric=_rubric())

    service.score(_require(sample), _run(), rubric=_rubric())

    prompt = provider.requests[0].messages[0].content
    assert "accuracy" in prompt
    assert provider.requests[0].model == "judge-model"
