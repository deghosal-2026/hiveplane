"""Tests for the spend read API (M22, #82)."""

from __future__ import annotations

from collections.abc import Sequence
from datetime import UTC, datetime

from fastapi.testclient import TestClient

from hiveplane.api.app import create_app
from hiveplane.budget.models import CostAttribution


def _attr(
    run_id: str,
    workload: str,
    *,
    team: str | None = "platform",
    cost: float = 1.0,
    model: str | None = "openai:gpt-4o",
) -> CostAttribution:
    return CostAttribution(
        run_id=run_id,
        workload=workload,
        team=team,
        model_identity=model,
        input_tokens=10,
        output_tokens=5,
        tool_calls=0,
        cost_usd=cost,
        timestamp=datetime(2026, 9, 19, 12, 0, tzinfo=UTC),
    )


def _client_with(attributions: Sequence[CostAttribution]) -> TestClient:
    app = create_app()
    for attribution in attributions:
        app.state.budget_store.record_attribution(attribution)
    return TestClient(app)


def test_spend_empty_store_returns_empty_lists() -> None:
    response = _client_with([]).get("/spend")

    assert response.status_code == 200
    assert response.json() == {"by_workload": [], "by_team": []}


def test_spend_aggregates_by_workload_and_team() -> None:
    client = _client_with(
        [
            _attr("r1", "agent-a", cost=1.0),
            _attr("r2", "agent-a", cost=2.0),
            _attr("r3", "agent-b", team="ops", cost=4.0),
        ]
    )

    body = client.get("/spend").json()

    assert body["by_workload"] == [
        {"workload": "agent-b", "team": "ops", "total_usd": 4.0, "run_count": 1},
        {"workload": "agent-a", "team": "platform", "total_usd": 3.0, "run_count": 2},
    ]
    assert body["by_team"] == [
        {"team": "ops", "total_usd": 4.0, "run_count": 1},
        {"team": "platform", "total_usd": 3.0, "run_count": 2},
    ]


def test_spend_counts_distinct_runs_not_attributions() -> None:
    client = _client_with(
        [
            _attr("r1", "agent-a", cost=0.5),
            _attr("r1", "agent-a", cost=0.25),
        ]
    )

    body = client.get("/spend").json()

    assert body["by_workload"][0]["run_count"] == 1
    assert body["by_workload"][0]["total_usd"] == 0.75


def test_spend_team_less_attribution_excluded_from_by_team() -> None:
    client = _client_with([_attr("r1", "agent-a", team=None, cost=1.0)])

    body = client.get("/spend").json()

    assert body["by_workload"] == [
        {"workload": "agent-a", "team": None, "total_usd": 1.0, "run_count": 1}
    ]
    assert body["by_team"] == []
