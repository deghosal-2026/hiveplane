"""Tests for the run feedback API (M36-01)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
from hiveplane.core.run import Run
from hiveplane.learning.feedback import FeedbackService
from hiveplane.learning.store import InMemoryFeedbackStore
from test_learning_feedback import _run, _Runs


def _client(runs: dict[str, Run]) -> TestClient:
    app = create_app()
    counter = {"n": 0}

    def _id() -> str:
        counter["n"] += 1
        return f"fb-{counter['n']}"

    app.state.feedback_service = FeedbackService(
        InMemoryFeedbackStore(),
        run_reader=_Runs(runs),
        id_factory=_id,
    )
    return TestClient(app)


def test_post_feedback_records_a_verdict() -> None:
    client = _client({"run-1": _run()})

    response = client.post(
        "/runs/run-1/feedback",
        json={"verdict": "failed-with-lesson", "notes": "escalate", "operator": "alice"},
    )

    assert response.status_code == 201
    body = response.json()
    assert body["run_id"] == "run-1"
    assert body["verdict"] == "failed-with-lesson"
    assert body["operator"] == "alice"
    assert body["workload_id"] == "repo-agent"


def test_post_feedback_rejects_non_terminal_run() -> None:
    from hiveplane.core.run import RunState

    client = _client({"run-1": _run(state=RunState.RUNNING)})

    response = client.post(
        "/runs/run-1/feedback", json={"verdict": "good", "operator": "alice"}
    )

    assert response.status_code == 409


def test_post_feedback_unknown_run_is_404() -> None:
    client = _client({})

    response = client.post(
        "/runs/ghost/feedback", json={"verdict": "good", "operator": "alice"}
    )

    assert response.status_code == 404


def test_post_failed_with_lesson_requires_notes() -> None:
    client = _client({"run-1": _run()})

    response = client.post(
        "/runs/run-1/feedback",
        json={"verdict": "failed-with-lesson", "operator": "alice"},
    )

    assert response.status_code == 422


def test_get_run_feedback_lists_records() -> None:
    client = _client({"run-1": _run()})
    client.post("/runs/run-1/feedback", json={"verdict": "good", "operator": "alice"})
    client.post("/runs/run-1/feedback", json={"verdict": "bad", "operator": "bob"})

    response = client.get("/runs/run-1/feedback")

    assert response.status_code == 200
    assert [item["verdict"] for item in response.json()] == ["good", "bad"]


def test_list_feedback_filters_by_workload() -> None:
    client = _client(
        {
            "run-1": _run("run-1", workload="repo-agent"),
            "run-2": _run("run-2", workload="docs-agent"),
        }
    )
    client.post("/runs/run-1/feedback", json={"verdict": "good", "operator": "alice"})
    client.post("/runs/run-2/feedback", json={"verdict": "bad", "operator": "bob"})

    response = client.get("/feedback?workload=repo-agent")

    assert [item["run_id"] for item in response.json()] == ["run-1"]
