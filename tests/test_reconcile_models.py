"""Unit tests for the reconcile domain models (M26-01/M26-02)."""

from __future__ import annotations

from datetime import UTC, datetime

import pytest
from pydantic import ValidationError

from hiveplane.fleet.reconcile import (
    DesiredSpec,
    SpecKind,
    SpecSource,
)
from hiveplane.reconcile.models import (
    ActionClass,
    ActionKind,
    ActionResult,
    ActionStatus,
    DesiredSet,
    ReconcileAction,
    ReconcileMode,
    ReconcileOutcome,
    ReconcilePlan,
    ReconcileRun,
)

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _spec(name: str = "agent-1", kind: SpecKind = SpecKind.WORKLOAD) -> DesiredSpec:
    return DesiredSpec(
        spec_id=f"{kind.value}/{name}",
        source_id="git-main",
        source=SpecSource.GIT,
        kind=kind,
        name=name,
        revision="abc123",
        content_hash="c" * 64,
        spec={},
        updated_at=_NOW,
    )


def test_desired_set_indexes_specs_by_kind_and_name() -> None:
    workload = _spec("agent-1", SpecKind.WORKLOAD)
    policy = _spec("platform-baseline", SpecKind.POLICY)
    desired = DesiredSet(
        source_id="git-main",
        source=SpecSource.GIT,
        revision="abc123",
        specs=[workload, policy],
        generated_at=_NOW,
    )
    assert desired.get(SpecKind.WORKLOAD, "agent-1") == workload
    assert desired.get(SpecKind.POLICY, "platform-baseline") == policy
    assert desired.get(SpecKind.TRIGGER, "missing") is None
    assert desired.names(SpecKind.WORKLOAD) == ["agent-1"]


def test_reconcile_action_marks_destructive_by_class() -> None:
    action = ReconcileAction(
        action_id="act-1",
        kind=ActionKind.DEREGISTER_WORKLOAD,
        object_kind=SpecKind.WORKLOAD,
        object_ref="agent-1",
        action_class=ActionClass.DESTRUCTIVE,
        summary="deregister agent-1",
    )
    assert action.destructive is True
    soft = action.model_copy(update={"action_class": ActionClass.SOFT})
    assert soft.destructive is False


def test_reconcile_plan_reports_destructive_and_sync() -> None:
    additive = ReconcileAction(
        action_id="act-1",
        kind=ActionKind.REGISTER_WORKLOAD,
        object_kind=SpecKind.WORKLOAD,
        object_ref="agent-1",
        action_class=ActionClass.ADDITIVE,
        summary="register agent-1",
    )
    destructive = additive.model_copy(
        update={"action_id": "act-2", "action_class": ActionClass.DESTRUCTIVE}
    )
    plan = ReconcilePlan(
        source_id="git-main",
        revision="abc123",
        mode=ReconcileMode.APPLY,
        actions=[additive, destructive],
        created_at=_NOW,
    )
    assert plan.destructive_actions == [destructive]
    assert plan.in_sync is False
    empty = plan.model_copy(update={"actions": []})
    assert empty.in_sync is True


def test_reconcile_run_derives_counts_from_action_results() -> None:
    run = ReconcileRun(
        run_id="rr-1",
        source_id="git-main",
        source=SpecSource.GIT,
        revision="abc123",
        mode=ReconcileMode.APPLY,
        started_at=_NOW,
        finished_at=_NOW,
        outcome=ReconcileOutcome.APPLIED,
        actions=[
            _result("act-1", ActionStatus.APPLIED),
            _result("act-2", ActionStatus.BLOCKED),
            _result("act-3", ActionStatus.FAILED),
            _result("act-4", ActionStatus.SKIPPED),
        ],
    )
    assert run.action_count == 4
    assert run.applied_count == 1
    assert run.blocked_count == 1
    assert run.failed_count == 1
    assert run.skipped_count == 1


def _result(action_id: str, status: ActionStatus) -> ActionResult:
    return ActionResult(
        action_id=action_id,
        kind=ActionKind.REGISTER_WORKLOAD,
        object_kind=SpecKind.WORKLOAD,
        object_ref="agent-1",
        status=status,
    )


def test_reconcile_action_requires_non_empty_ref() -> None:
    with pytest.raises(ValidationError):
        ReconcileAction(
            action_id="act-1",
            kind=ActionKind.REGISTER_WORKLOAD,
            object_kind=SpecKind.WORKLOAD,
            object_ref="",
            action_class=ActionClass.ADDITIVE,
            summary="register",
        )
