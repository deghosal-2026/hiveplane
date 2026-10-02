"""Router API, store, and accuracy tests (M30-01..M30-03, #199-#201)."""

from __future__ import annotations

from datetime import UTC, datetime

from fastapi.testclient import TestClient
from sqlalchemy import Engine

from hiveplane.api.app import create_app
from hiveplane.core.run import AdmissionContext
from hiveplane.router.catalog import CatalogCandidate, StaticCatalog
from hiveplane.router.classifier import ClassificationResult
from hiveplane.router.engine import RouterEngine
from hiveplane.router.models import RouteDecision, RouteOutcome, RouterCandidate
from hiveplane.router.store import (
    InMemoryRouterStore,
    PostgresRouterStore,
    RouterStore,
)
from postgres import reset_database

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


class KeywordClassifier:
    """A deterministic stand-in for the cheap classifier in tests."""

    def __init__(self, mapping: dict[str, list[str]]) -> None:
        self._mapping = mapping

    def classify(
        self, task: str, candidates: list[CatalogCandidate]
    ) -> ClassificationResult:
        lowered = task.lower()
        scores = {candidate.workload: 0.0 for candidate in candidates}
        for workload, keywords in self._mapping.items():
            if workload in scores and any(keyword in lowered for keyword in keywords):
                scores[workload] = 0.9
        return ClassificationResult(model_identity="keyword-v1", scores=scores)


def _decision() -> RouteDecision:
    return RouteDecision(
        decision_id="route-1",
        task_hash="abc",
        context=AdmissionContext.STAGING,
        classifier_model="keyword-v1",
        candidates=[RouterCandidate(workload="triage", score=0.9, rank=1)],
        chosen="triage",
        outcome=RouteOutcome.ROUTED,
        confidence_threshold=0.6,
        margin=0.15,
        created_at=_NOW,
    )


def _assert_store(store: RouterStore) -> None:
    store.save_decision(_decision())
    loaded = store.get_decision("route-1")
    assert loaded is not None and loaded.chosen == "triage"
    assert store.get_decision("missing") is None
    assert [d.decision_id for d in store.list_decisions()] == ["route-1"]

    refused = _decision().model_copy(
        update={"outcome": RouteOutcome.REFUSED, "chosen": None}
    )
    store.save_decision(refused)
    updated = store.get_decision("route-1")
    assert updated is not None and updated.outcome is RouteOutcome.REFUSED

    store.clear()
    assert store.list_decisions() == []


def test_in_memory_router_store() -> None:
    _assert_store(InMemoryRouterStore())


def test_postgres_router_store(pg_engine: Engine) -> None:
    reset_database(pg_engine)
    _assert_store(PostgresRouterStore(pg_engine))


def _client(mapping: dict[str, list[str]]) -> TestClient:
    app = create_app()
    app.state.router_engine = RouterEngine(
        StaticCatalog(["triage", "remediate", "notify"]),
        KeywordClassifier(mapping),
        store=app.state.router_store,
    )
    return TestClient(app)


def test_route_api_routes_and_explains() -> None:
    client = _client({"triage": ["database", "down"]})
    response = client.post("/route", json={"task": "the database is down"})
    assert response.status_code == 200
    body = response.json()
    assert body["outcome"] == "routed"
    assert body["chosen"] == "triage"
    assert body["candidates"][0]["score"] == 0.9

    decision_id = body["decision_id"]
    assert client.get(f"/routes/{decision_id}").json()["chosen"] == "triage"
    assert client.get("/routes").json()[0]["decision_id"] == decision_id
    assert client.get("/routes/missing").status_code == 404


def test_route_api_refuses_low_confidence() -> None:
    client = _client({})
    body = client.post("/route", json={"task": "unrecognized request"}).json()
    assert body["outcome"] == "refused"
    assert body["reason"] == "low_confidence"
    assert body["chosen"] is None


def test_route_api_disabled_returns_503() -> None:
    client = TestClient(create_app())
    assert client.post("/route", json={"task": "x"}).status_code == 503


def test_route_api_rejects_empty_task() -> None:
    client = _client({})
    assert client.post("/route", json={"task": ""}).status_code == 422


def test_routing_accuracy_on_labeled_set() -> None:
    labeled = {
        "the database is down": "triage",
        "remediate the failed service": "remediate",
        "notify the on-call channel": "notify",
        "triage this production incident": "triage",
        "send a remediation for the host": "remediate",
        "notify the team about the incident": "notify",
    }
    engine = RouterEngine(
        StaticCatalog(["triage", "remediate", "notify"]),
        KeywordClassifier(
            {
                "triage": ["triage", "down", "incident"],
                "remediate": ["remediate", "remediation", "failed"],
                "notify": ["notify", "channel", "team"],
            }
        ),
        confidence_threshold=0.6,
        margin=0.15,
    )
    correct = sum(
        1 for task, expected in labeled.items() if engine.route(task).chosen == expected
    )
    assert correct / len(labeled) >= 0.8
