"""Tests for the corpus-candidate and review API (M36-02, M36-03)."""

from __future__ import annotations

from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
from hiveplane.learning.candidate_store import InMemoryCandidateStore
from hiveplane.learning.candidates import CandidateService
from hiveplane.learning.feedback import FeedbackService
from hiveplane.learning.store import InMemoryFeedbackStore
from test_learning_feedback import _run, _Runs


def _client() -> TestClient:
    app = create_app()
    counter = {"n": 0}

    def _id() -> str:
        counter["n"] += 1
        return f"cand-{counter['n']}"

    candidates = CandidateService(
        InMemoryCandidateStore(),
        id_factory=_id,
        review_id_factory=lambda: f"rev-{counter['n']}",
    )
    feedback = FeedbackService(
        InMemoryFeedbackStore(),
        run_reader=_Runs({"run-1": _run()}),
        id_factory=lambda: "fb-1",
        candidates=candidates,
    )
    app.state.candidate_service = candidates
    app.state.feedback_service = feedback
    return TestClient(app)


def _flag_failure(client: TestClient) -> None:
    response = client.post(
        "/runs/run-1/feedback",
        json={
            "verdict": "failed-with-lesson",
            "notes": "should escalate",
            "operator": "alice",
        },
    )
    assert response.status_code == 201


def test_failed_with_lesson_feedback_creates_a_pending_candidate() -> None:
    client = _client()

    _flag_failure(client)

    response = client.get("/corpus/candidates?workload=repo-agent")
    assert response.status_code == 200
    body = response.json()
    assert len(body) == 1
    assert body[0]["status"] == "pending"
    assert body[0]["source_run_id"] == "run-1"
    assert body[0]["expected"]["outcome"] == "should escalate"


def test_approve_candidate() -> None:
    client = _client()
    _flag_failure(client)

    response = client.post(
        "/corpus/candidates/cand-1/approve", json={"reviewer": "bob"}
    )

    assert response.status_code == 200
    assert response.json()["status"] == "approved"


def test_reject_candidate_requires_a_reason() -> None:
    client = _client()
    _flag_failure(client)

    response = client.post(
        "/corpus/candidates/cand-1/reject", json={"reviewer": "bob"}
    )

    assert response.status_code == 422


def test_reject_candidate_archives_with_reason() -> None:
    client = _client()
    _flag_failure(client)

    response = client.post(
        "/corpus/candidates/cand-1/reject",
        json={"reviewer": "bob", "reason": "wrong expectation"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "rejected"


def test_approve_unknown_candidate_is_404() -> None:
    client = _client()

    response = client.post(
        "/corpus/candidates/ghost/approve", json={"reviewer": "bob"}
    )

    assert response.status_code == 404


def test_double_review_is_conflict() -> None:
    client = _client()
    _flag_failure(client)
    client.post("/corpus/candidates/cand-1/approve", json={"reviewer": "bob"})

    response = client.post(
        "/corpus/candidates/cand-1/reject",
        json={"reviewer": "bob", "reason": "late"},
    )

    assert response.status_code == 409
