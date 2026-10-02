"""Unit tests for the reconcile planner and its guardrails (M26-02/M26-05)."""

from __future__ import annotations

from datetime import UTC, datetime

from hiveplane.fleet.reconcile import SpecKind
from hiveplane.reconcile.differ import Delta, DeltaKind, DiffResult
from hiveplane.reconcile.models import ActionClass, ActionKind, ReconcileMode, ReconcilePlan
from hiveplane.reconcile.planner import Guardrails, Planner

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _delta(kind: DeltaKind, ref: str = "agent-1", recert: bool = False) -> Delta:
    return Delta(
        kind=kind,
        object_kind=SpecKind.WORKLOAD,
        object_ref=ref,
        recert=recert,
    )


def _plan(
    diff: DiffResult,
    *,
    guardrails: Guardrails | None = None,
    desired_count: int = 1,
    first_run: bool = False,
    confirmed: bool = False,
) -> ReconcilePlan:
    return Planner(guardrails or Guardrails()).plan(
        diff,
        source_id="git-main",
        revision="rev-1",
        mode=ReconcileMode.APPLY,
        created_at=_NOW,
        desired_count=desired_count,
        first_run=first_run,
        confirmed=confirmed,
    )


def test_additive_and_soft_actions_are_never_blocked() -> None:
    diff = DiffResult(deltas=[_delta(DeltaKind.CREATE), _delta(DeltaKind.UPDATE)])
    plan = _plan(diff)
    assert [action.kind for action in plan.actions] == [
        ActionKind.REGISTER_WORKLOAD,
        ActionKind.UPDATE_WORKLOAD,
    ]
    assert all(not action.blocked for action in plan.actions)


def test_destructive_actions_are_blocked_by_default() -> None:
    diff = DiffResult(deltas=[_delta(DeltaKind.DEREGISTER)])
    plan = _plan(diff, desired_count=0)
    action = plan.actions[0]
    assert action.kind is ActionKind.DEREGISTER_WORKLOAD
    assert action.action_class is ActionClass.DESTRUCTIVE
    assert action.blocked is True
    assert "not permitted" in (action.block_reason or "")


def test_destructive_actions_allowed_when_permitted() -> None:
    diff = DiffResult(deltas=[_delta(DeltaKind.DEREGISTER)])
    plan = _plan(
        diff,
        guardrails=Guardrails(allow_destructive=True, allow_empty=True),
        desired_count=0,
    )
    assert plan.actions[0].blocked is False


def test_empty_desired_set_blocks_destructive_cascade() -> None:
    diff = DiffResult(deltas=[_delta(DeltaKind.QUARANTINE)])
    plan = _plan(
        diff, guardrails=Guardrails(allow_destructive=True), desired_count=0
    )
    assert plan.actions[0].blocked is True
    assert "empty desired set" in (plan.actions[0].block_reason or "")


def test_destructive_rate_limit_caps_actions() -> None:
    diff = DiffResult(
        deltas=[_delta(DeltaKind.DEREGISTER, ref=f"agent-{i}") for i in range(5)]
    )
    plan = _plan(
        diff,
        guardrails=Guardrails(
            allow_destructive=True, allow_empty=True, max_destructive_per_run=2
        ),
        desired_count=0,
    )
    blocked = [action for action in plan.actions if action.blocked]
    assert len(blocked) == 3
    assert "rate limit" in (blocked[0].block_reason or "")


def test_first_run_requires_confirmation_for_destructive_actions() -> None:
    diff = DiffResult(deltas=[_delta(DeltaKind.DEREGISTER)])
    plan = _plan(
        diff,
        guardrails=Guardrails(allow_destructive=True, allow_empty=True),
        desired_count=0,
        first_run=True,
    )
    assert plan.actions[0].blocked is True
    assert "confirmation" in (plan.actions[0].block_reason or "")


def test_recert_delta_emits_update_and_recertify_actions() -> None:
    diff = DiffResult(deltas=[_delta(DeltaKind.UPDATE, recert=True)])
    plan = _plan(diff)
    assert [action.kind for action in plan.actions] == [
        ActionKind.UPDATE_WORKLOAD,
        ActionKind.RECERTIFY_WORKLOAD,
    ]


def test_empty_diff_is_in_sync() -> None:
    plan = _plan(DiffResult())
    assert plan.in_sync is True
    assert plan.actions == []
