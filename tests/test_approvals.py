"""Tests for the approval service."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest

from hiveplane.core.approval import ApprovalStatus
from hiveplane.core.decision import ActionClass
from hiveplane.policy.approvals import ApprovalService
from hiveplane.policy.errors import ApprovalAlreadyDecidedError, ApprovalNotFoundError
from hiveplane.policy.store import InMemoryApprovalStore


def _clock() -> datetime:
    return datetime(2026, 1, 1, tzinfo=UTC)


def _service() -> ApprovalService:
    ids = iter(["ap-1", "ap-2"])
    return ApprovalService(InMemoryApprovalStore(), clock=_clock, id_factory=lambda: next(ids))


def test_request_creates_pending_record() -> None:
    service = _service()
    record = service.request(
        run_id="run-1",
        workload="agent-1",
        rule="approvals.required",
        reason="needs approval",
        action_class=ActionClass.DESTRUCTIVE,
    )
    assert record.approval_id == "ap-1"
    assert record.status is ApprovalStatus.PENDING
    assert service.get("ap-1").run_id == "run-1"


def test_get_missing_raises() -> None:
    with pytest.raises(ApprovalNotFoundError):
        _service().get("nope")


def test_approve_records_decision() -> None:
    service = _service()
    service.request(run_id="run-1", workload="agent-1", rule="r", reason="x")
    decided = service.decide("ap-1", status=ApprovalStatus.APPROVED, operator="alice")
    assert decided.status is ApprovalStatus.APPROVED
    assert decided.decided_by == "alice"
    assert decided.decided_at == _clock()


def test_deny_records_reason() -> None:
    service = _service()
    service.request(run_id="run-1", workload="agent-1", rule="r", reason="x")
    decided = service.decide(
        "ap-1", status=ApprovalStatus.DENIED, operator="bob", reason="too risky"
    )
    assert decided.status is ApprovalStatus.DENIED
    assert decided.decision_reason == "too risky"


def test_double_decide_raises() -> None:
    service = _service()
    service.request(run_id="run-1", workload="agent-1", rule="r", reason="x")
    service.decide("ap-1", status=ApprovalStatus.APPROVED, operator="alice")
    with pytest.raises(ApprovalAlreadyDecidedError):
        service.decide("ap-1", status=ApprovalStatus.DENIED, operator="bob")


def test_decide_rejects_pending_status() -> None:
    service = _service()
    service.request(run_id="run-1", workload="agent-1", rule="r", reason="x")
    with pytest.raises(ValueError):
        service.decide("ap-1", status=ApprovalStatus.PENDING, operator="alice")


def test_list_filters() -> None:
    service = _service()
    service.request(run_id="run-1", workload="agent-1", rule="r", reason="x")
    service.request(run_id="run-2", workload="agent-2", rule="r", reason="x")
    service.decide("ap-1", status=ApprovalStatus.APPROVED, operator="alice")
    assert {a.approval_id for a in service.list(status=ApprovalStatus.PENDING)} == {"ap-2"}
    assert {a.approval_id for a in service.list(workload="agent-2")} == {"ap-2"}
