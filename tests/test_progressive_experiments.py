"""Tests for model experiment campaigns (M38-06)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from sqlalchemy import Engine

from hiveplane.progressive.errors import CanaryNotAllowedError, ExperimentNotFoundError
from hiveplane.progressive.experiments import ExperimentService
from hiveplane.progressive.models import ExperimentState
from hiveplane.progressive.store import (
    InMemoryProgressiveStore,
    PostgresProgressiveStore,
)
from postgres import ensure_schema

_NOW = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)


def _service() -> ExperimentService:
    counter = {"n": 0}

    def _id(prefix: str) -> str:
        counter["n"] += 1
        return f"{prefix}-{counter['n']}"

    return ExperimentService(
        InMemoryProgressiveStore(),
        clock=lambda: _NOW,
        id_factory=lambda: _id("campaign"),
        arm_id_factory=lambda: _id("arm"),
    )


def test_start_creates_campaign_and_arms() -> None:
    service = _service()

    campaign = service.start("repo-agent", ["gpt-4o", "gpt-4o-mini"])

    assert campaign.state is ExperimentState.RUNNING
    arms = service.arms(campaign.campaign_id)
    assert [arm.model_identity for arm in arms] == ["gpt-4o", "gpt-4o-mini"]


def test_start_requires_at_least_two_arms() -> None:
    service = _service()

    with pytest.raises(CanaryNotAllowedError):
        service.start("repo-agent", ["gpt-4o"])


def test_select_winner_picks_the_highest_score_with_evidence() -> None:
    service = _service()
    campaign = service.start("repo-agent", ["gpt-4o", "gpt-4o-mini"])
    arms = service.arms(campaign.campaign_id)
    service.record_result(arms[0].arm_id, benchmark_run_id="br-1", score=0.7)
    service.record_result(arms[1].arm_id, benchmark_run_id="br-2", score=0.9)

    updated = service.select_winner(campaign.campaign_id)

    assert updated.state is ExperimentState.COMPLETED
    assert updated.winner_arm_id == arms[1].arm_id
    assert "gpt-4o-mini" in (updated.rationale or "")
    assert "br-2" in (updated.rationale or "")


def test_select_winner_excludes_unscored_arms() -> None:
    service = _service()
    campaign = service.start("repo-agent", ["gpt-4o", "gpt-4o-mini"])
    arms = service.arms(campaign.campaign_id)
    service.record_result(arms[0].arm_id, benchmark_run_id="br-1", score=0.6)
    service.record_result(arms[1].arm_id, benchmark_run_id="br-2", score=None)

    updated = service.select_winner(campaign.campaign_id)

    assert updated.winner_arm_id == arms[0].arm_id


def test_select_winner_with_no_scores_records_no_winner() -> None:
    service = _service()
    campaign = service.start("repo-agent", ["gpt-4o", "gpt-4o-mini"])

    updated = service.select_winner(campaign.campaign_id)

    assert updated.winner_arm_id is None
    assert updated.state is ExperimentState.COMPLETED


def test_get_unknown_campaign_raises() -> None:
    service = _service()

    with pytest.raises(ExperimentNotFoundError):
        service.get("ghost")


def test_record_result_unknown_arm_raises() -> None:
    service = _service()

    with pytest.raises(ExperimentNotFoundError):
        service.record_result("ghost", benchmark_run_id="br-1", score=0.5)


def test_postgres_experiment_round_trip(pg_engine: Engine) -> None:
    ensure_schema(pg_engine)
    store = PostgresProgressiveStore(pg_engine)
    store.clear()
    service = ExperimentService(
        store,
        clock=lambda: _NOW,
        id_factory=lambda: "campaign-1",
        arm_id_factory=lambda: "arm-1",
    )
    campaign = service.start("repo-agent", ["gpt-4o", "gpt-4o-mini"])
    arm = service.arms(campaign.campaign_id)[0]
    service.record_result(arm.arm_id, benchmark_run_id="br-1", score=0.8)

    reopened = PostgresProgressiveStore(pg_engine)

    assert reopened.get_campaign("campaign-1") is not None
    assert reopened.list_arms("campaign-1")[0].score == 0.8
