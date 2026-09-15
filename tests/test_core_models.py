"""Tests for core run, event, usage, and policy-decision models."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from hiveplane.core.decision import ActionClass, DecisionOutcome, PolicyDecision
from hiveplane.core.event import EventType, RunEvent
from hiveplane.core.run import Run, RunState, TriggerOrigin, can_transition
from hiveplane.core.usage import UsageReport

NOW = datetime(2026, 9, 12, 10, 0, 0, tzinfo=UTC)


def _run(**overrides: object) -> Run:
    base: dict[str, object] = {
        "id": "run-1",
        "workload_id": "repo-agent",
        "caller": "ci@hiveplane",
        "state": RunState.QUEUED,
        "model_identity": "gpt-4o-2024-08-06",
        "created_at": NOW,
        "updated_at": NOW,
    }
    base.update(overrides)
    return Run.model_validate(base)


def test_run_construction_and_defaults() -> None:
    run = _run()

    assert run.state is RunState.QUEUED
    assert run.trigger_origin is None
    assert run.started_at is None


def test_run_rejects_naive_timestamp() -> None:
    with pytest.raises(ValidationError, match="timezone"):
        _run(created_at=datetime(2026, 9, 12, 10, 0, 0))  # noqa: DTZ001


def test_run_rejects_unknown_state() -> None:
    with pytest.raises(ValidationError):
        _run(state="exploded")


def test_trigger_origin_requires_fields() -> None:
    origin = TriggerOrigin(source="pagerduty", event_id="evt-1", timestamp=NOW)

    assert origin.source == "pagerduty"


@pytest.mark.parametrize(
    ("current", "target", "allowed"),
    [
        (RunState.QUEUED, RunState.RUNNING, True),
        (RunState.RUNNING, RunState.PAUSED, True),
        (RunState.PAUSED, RunState.RUNNING, True),
        (RunState.RUNNING, RunState.COMPLETED, True),
        (RunState.RUNNING, RunState.FAILED, True),
        (RunState.RUNNING, RunState.CANCELLED, True),
        (RunState.PAUSED, RunState.CANCELLED, True),
        (RunState.COMPLETED, RunState.RUNNING, False),
        (RunState.QUEUED, RunState.COMPLETED, False),
        (RunState.CANCELLED, RunState.RUNNING, False),
    ],
)
def test_can_transition(current: RunState, target: RunState, allowed: bool) -> None:
    assert can_transition(current, target) is allowed


def test_run_event_has_attribution() -> None:
    event = RunEvent(
        run_id="run-1",
        sequence=0,
        type=EventType.STATE_CHANGE,
        from_state=RunState.QUEUED,
        to_state=RunState.RUNNING,
        actor="adapter:raw-worker",
        timestamp=NOW,
    )

    assert event.actor == "adapter:raw-worker"


def test_run_event_requires_non_negative_sequence() -> None:
    with pytest.raises(ValidationError):
        RunEvent(
            run_id="run-1",
            sequence=-1,
            type=EventType.STATE_CHANGE,
            actor="system",
            timestamp=NOW,
        )


def test_usage_report_totals_and_validation() -> None:
    usage = UsageReport(
        run_id="run-1",
        input_tokens=100,
        output_tokens=50,
        tool_calls=3,
        cost_usd=0.25,
        timestamp=NOW,
    )

    assert usage.total_tokens == 150


def test_usage_report_rejects_negative_tokens() -> None:
    with pytest.raises(ValidationError):
        UsageReport(
            run_id="run-1",
            input_tokens=-1,
            output_tokens=0,
            tool_calls=0,
            cost_usd=0.0,
            timestamp=NOW,
        )


def test_policy_decision() -> None:
    decision = PolicyDecision(
        run_id="run-1",
        outcome=DecisionOutcome.ESCALATE,
        reason="destructive tool requires approval",
        rule="tools.destructive.require_approval",
        action_class=ActionClass.DESTRUCTIVE,
        timestamp=NOW,
    )

    assert decision.outcome is DecisionOutcome.ESCALATE
    assert decision.action_class is ActionClass.DESTRUCTIVE


def test_policy_decision_rejects_unknown_outcome() -> None:
    with pytest.raises(ValidationError):
        PolicyDecision.model_validate(
            {
                "run_id": "run-1",
                "outcome": "maybe",
                "reason": "x",
                "rule": "r",
                "timestamp": NOW.isoformat(),
            }
        )
