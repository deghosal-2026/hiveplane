"""Tests for the budget store."""

from __future__ import annotations

from datetime import UTC, datetime

from hiveplane.budget.models import CostAttribution
from hiveplane.budget.store import InMemoryBudgetStore


def _clock() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def test_run_day_team_spend_accumulates() -> None:
    store = InMemoryBudgetStore()
    store.add_run_spend("run-1", 0.25)
    store.add_run_spend("run-1", 0.25)
    store.add_day_spend("agent-1", "2026-01-01", 0.5)
    store.add_team_spend("platform", "2026-01-01", 0.5)
    assert store.run_spend("run-1") == 0.5
    assert store.day_spend("agent-1", "2026-01-01") == 0.5
    assert store.team_spend("platform", "2026-01-01") == 0.5
    assert store.run_spend("missing") == 0.0
    assert store.day_spend("agent-1", "2026-01-02") == 0.0
    assert store.team_spend("platform", "2026-01-02") == 0.0


def test_attributions_round_trip() -> None:
    store = InMemoryBudgetStore()
    store.record_attribution(
        CostAttribution(
            run_id="run-1",
            workload="agent-1",
            team="platform",
            model_identity="openai/gpt-4o/2024-08-06",
            input_tokens=100,
            output_tokens=50,
            tool_calls=1,
            cost_usd=0.02,
            timestamp=_clock(),
        )
    )
    records = store.list_attributions(workload="agent-1")
    assert len(records) == 1
    assert records[0].cost_usd == 0.02
    assert store.list_attributions(workload="other") == []
