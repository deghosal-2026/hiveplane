"""Tests for execution models and errors."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from hiveplane.core.fanout import FanOutType
from hiveplane.core.run import AdmissionContext, RunState
from hiveplane.execution.errors import (
    IllegalTransitionError,
    RunAdmissionRefusedError,
    RunNotFoundError,
    RunNotIntervenableError,
)
from hiveplane.execution.models import (
    AdmissionCheck,
    AdmissionOutcome,
    AdmissionResult,
    DeliveryRecord,
    DeliveryStatus,
    InterventionAction,
    RunSubmission,
)


def _result(**overrides: object) -> AdmissionResult:
    data: dict[str, object] = {
        "run_id": "run-1",
        "workload": "agent-1",
        "context": AdmissionContext.PRODUCTION,
        "outcome": AdmissionOutcome.REFUSED,
        "refused_reason": "not certified",
    }
    data.update(overrides)
    return AdmissionResult.model_validate(data)


def test_admission_result_defaults() -> None:
    result = _result()
    assert result.outcome is AdmissionOutcome.REFUSED
    assert result.checks == []
    assert result.sandbox is False
    assert result.escalation_required is False


def test_admission_check_records_step() -> None:
    check = AdmissionCheck(step="certification", passed=False, reason="uncertified", rule="cert")
    assert check.passed is False
    assert check.rule == "cert"


def test_intervention_actions() -> None:
    assert {a.value for a in InterventionAction} == {"pause", "resume", "stop"}


def test_delivery_record_requires_target() -> None:
    record = DeliveryRecord(
        run_id="run-1",
        destination_type=FanOutType.WEBHOOK,
        target="https://example.test/hook",
        status=DeliveryStatus.DELIVERED,
        attempts=1,
        timestamp=datetime(2026, 1, 1, tzinfo=UTC),
    )
    assert record.attempts == 1
    with pytest.raises(ValidationError):
        DeliveryRecord(
            run_id="run-1",
            destination_type=FanOutType.WEBHOOK,
            target="",
            status=DeliveryStatus.PENDING,
            attempts=0,
            timestamp=datetime(2026, 1, 1, tzinfo=UTC),
        )


def test_run_submission_defaults() -> None:
    submission = RunSubmission(workload="agent-1", caller="cli", context=AdmissionContext.SANDBOX)
    assert submission.task == {}
    assert submission.model_identity is None
    assert submission.context is AdmissionContext.SANDBOX


def test_errors_carry_context() -> None:
    assert RunNotFoundError("run-1").run_id == "run-1"
    illegal = IllegalTransitionError("run-1", RunState.COMPLETED, RunState.RUNNING)
    assert illegal.current is RunState.COMPLETED
    refused = RunAdmissionRefusedError(_result())
    assert "agent-1" in str(refused)
    assert RunNotIntervenableError("run-1", "pause").run_id == "run-1"
