"""Tests for the online-eval and quality API (M36-05, M36-06)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
from hiveplane.learning.eval import EvalService
from hiveplane.learning.eval_store import InMemoryEvalStore
from hiveplane.learning.judge import RubricJudge
from hiveplane.learning.rubrics import RubricRegistry, default_rubric
from test_learning_eval import _FakeProvider
from test_learning_feedback import _run


def _client(score: float = 0.4) -> TestClient:
    app = create_app()
    store = InMemoryEvalStore()
    service = EvalService(
        store,
        judge=RubricJudge(
            _FakeProvider(f'{{"score": {score}, "criteria": []}}'), model="judge-model"
        ),
        rubrics=RubricRegistry(store),
        id_factory=lambda: "sample-1",
        score_id_factory=lambda: "score-1",
        quality_target=0.8,
    )
    app.state.eval_service = service
    sample = service.maybe_sample(_run(), sample_rate=100, rubric=default_rubric())
    assert sample is not None
    service.score(sample, _run(), rubric=default_rubric())
    return TestClient(app)


def test_list_eval_samples() -> None:
    client = _client()

    response = client.get("/eval/samples?workload=repo-agent")

    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["run_id"] == "run-1"
    assert body[0]["rubric_version"] == 1


def test_workload_quality_reports_a_dip() -> None:
    client = _client(score=0.4)
    # the app's eval service uses settings.eval.quality_target (0.8)
    response = client.get("/workloads/repo-agent/quality")

    assert response.status_code == 200
    body = response.json()
    assert body["sample_count"] == 1
    assert body["mean_score"] == 0.4
    assert body["dip"] is True


def test_workload_quality_empty_state() -> None:
    app = create_app()
    store = InMemoryEvalStore()
    app.state.eval_service = EvalService(
        store,
        judge=RubricJudge(_FakeProvider("{}"), model="judge-model"),
        rubrics=RubricRegistry(store),
    )
    client = TestClient(app)

    response = client.get("/workloads/ghost/quality")

    assert response.status_code == 200
    assert response.json()["sample_count"] == 0
    assert response.json()["dip"] is False
