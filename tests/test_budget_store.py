"""Tests for the budget store."""

from __future__ import annotations

from datetime import UTC, datetime

from sqlalchemy import Engine

from hiveplane.budget.models import CostAttribution
from hiveplane.budget.store import (
    InMemoryBudgetStore,
    PostgresBudgetStore,
    build_budget_store,
)
from hiveplane.config import Settings
from hiveplane.persistence.base import create_engine_from_settings


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


def test_builder_defaults_to_in_memory() -> None:
    assert isinstance(build_budget_store(Settings()), InMemoryBudgetStore)


def test_builder_selects_postgres() -> None:
    settings = Settings.model_validate({"execution": {"store": "postgres"}})
    assert isinstance(build_budget_store(settings), PostgresBudgetStore)


def _attribution(cost: float = 0.02) -> CostAttribution:
    return CostAttribution(
        run_id="run-1",
        workload="agent-1",
        team="platform",
        model_identity="openai/gpt-4o/2024-08-06",
        input_tokens=100,
        output_tokens=50,
        tool_calls=1,
        cost_usd=cost,
        timestamp=_clock(),
    )


def test_postgres_spend_round_trip(pg_engine: Engine) -> None:
    store = PostgresBudgetStore(pg_engine)
    store.clear()
    store.add_run_spend("run-1", 0.25)
    store.add_run_spend("run-1", 0.25)
    store.add_day_spend("agent-1", "2026-01-01", 0.5)
    store.add_team_spend("platform", "2026-01-01", 0.5)
    store.record_attribution(_attribution())

    assert store.run_spend("run-1") == 0.5
    assert store.day_spend("agent-1", "2026-01-01") == 0.5
    assert store.team_spend("platform", "2026-01-01") == 0.5
    assert store.day_spend("agent-1", "2026-01-02") == 0.0
    assert store.list_attributions(workload="agent-1")[0].cost_usd == 0.02
    assert store.list_attributions(workload="other") == []


def test_postgres_daily_aggregate_survives_new_store_instance(pg_engine: Engine) -> None:
    store = PostgresBudgetStore(pg_engine)
    store.clear()
    store.add_day_spend("agent-1", "2026-01-01", 0.75)
    store.add_team_spend("platform", "2026-01-01", 0.75)

    fresh_engine = create_engine_from_settings()
    try:
        reopened = PostgresBudgetStore(fresh_engine)
        assert reopened.day_spend("agent-1", "2026-01-01") == 0.75
        assert reopened.team_spend("platform", "2026-01-01") == 0.75
    finally:
        fresh_engine.dispose()
