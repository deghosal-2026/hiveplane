"""Tests for the extended run, decision, and usage models."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from hiveplane.core.decision import (
    ActionClass,
    BlastRadius,
    DataSensitivity,
    DecisionOutcome,
    PolicyContext,
    PolicyDecision,
)
from hiveplane.core.run import AdmissionContext, Run, RunState
from hiveplane.core.usage import BudgetCheck, BudgetLevel


def _run(**overrides: object) -> Run:
    now = datetime(2026, 1, 1, tzinfo=UTC)
    data: dict[str, object] = {
        "id": "run-1",
        "workload_id": "agent-1",
        "caller": "cli",
        "state": RunState.QUEUED,
        "created_at": now,
        "updated_at": now,
    }
    data.update(overrides)
    return Run.model_validate(data)


def test_run_defaults_for_execution_fields() -> None:
    run = _run()
    assert run.manifest_version is None
    assert run.context is None
    assert run.sandbox is False
    assert run.task == {}
    assert run.result is None
    assert run.failure_reason is None
    assert run.cost_usd == 0.0


def test_run_accepts_execution_fields() -> None:
    run = _run(
        manifest_version=3,
        context=AdmissionContext.PRODUCTION,
        sandbox=True,
        task={"alert": "high"},
        cost_usd=0.25,
    )
    assert run.context is AdmissionContext.PRODUCTION
    assert run.sandbox is True
    assert run.task == {"alert": "high"}
    assert run.cost_usd == 0.25


def test_admission_context_values() -> None:
    assert {c.value for c in AdmissionContext} == {"sandbox", "staging", "production"}


def test_decision_outcome_includes_block_injection() -> None:
    assert DecisionOutcome.BLOCK_INJECTION.value == "block_injection"


def test_policy_context_and_decision() -> None:
    context = PolicyContext(
        run_id="run-1",
        workload="agent-1",
        environment=AdmissionContext.PRODUCTION,
        action_class=ActionClass.DESTRUCTIVE,
        data_sensitivity=DataSensitivity.PII,
    )
    assert context.data_sensitivity is DataSensitivity.PII
    decision = PolicyDecision(
        run_id="run-1",
        outcome=DecisionOutcome.ALLOW,
        reason="ok",
        rule="manifest.allow",
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        blast_radius=BlastRadius(score=42, factors={"trust": 20}),
        certification_status=context.certification_status,
    )
    assert decision.blast_radius is not None
    assert decision.blast_radius.score == 42


def test_blast_radius_bounds() -> None:
    with pytest.raises(ValidationError):
        BlastRadius(score=101)


def test_budget_check_bounds() -> None:
    check = BudgetCheck(
        allowed=False,
        level=BudgetLevel.DAY,
        limit_usd=5.0,
        spent_usd=5.0,
        remaining_usd=0.0,
        reason="day budget exhausted",
    )
    assert check.allowed is False
    with pytest.raises(ValidationError):
        BudgetCheck(
            allowed=True,
            level=BudgetLevel.RUN,
            limit_usd=1.0,
            spent_usd=0.0,
            remaining_usd=-1.0,
        )
