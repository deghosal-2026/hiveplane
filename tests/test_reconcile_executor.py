"""Action executor tests and registry reconcile-support methods (M26-03)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

import pytest

from hiveplane.certification.models import CertificationStatus
from hiveplane.fleet.reconcile import DesiredSpec, SpecKind, SpecSource
from hiveplane.reconcile.differ import Delta, DeltaKind, DiffResult
from hiveplane.reconcile.executor import ActionExecutor
from hiveplane.reconcile.models import (
    ActionClass,
    ActionKind,
    ActionResult,
    ActionStatus,
    ReconcileAction,
)
from hiveplane.registry.errors import WorkloadNotFoundError
from hiveplane.registry.service import RegistryService
from hiveplane.registry.store import InMemoryRegistryStore

_NOW = datetime(2026, 1, 1, tzinfo=UTC)


def _manifest(make_manifest: Any, name: str = "agent-1", **spec: Any) -> Any:
    return make_manifest(name=name, **spec)


def _desired(manifest: Any) -> DesiredSpec:
    return DesiredSpec(
        spec_id=f"workload/{manifest.name}",
        source_id="git-main",
        source=SpecSource.GIT,
        kind=SpecKind.WORKLOAD,
        name=manifest.name,
        revision="rev-1",
        content_hash="c" * 64,
        spec=manifest.model_dump(by_alias=True, mode="json", exclude_none=True),
        updated_at=_NOW,
    )


def _action(
    kind: ActionKind, ref: str = "agent-1", **overrides: Any
) -> ReconcileAction:
    base: dict[str, Any] = {
        "action_id": f"act-{kind.value}",
        "kind": kind,
        "object_kind": SpecKind.WORKLOAD,
        "object_ref": ref,
        "action_class": ActionClass.ADDITIVE,
        "summary": f"{kind.value} {ref}",
    }
    base.update(overrides)
    return ReconcileAction(**base)


def test_register_action_creates_workload_and_is_idempotent(make_manifest: Any) -> None:
    registry = RegistryService(InMemoryRegistryStore())
    executor = ActionExecutor(registry)
    manifest = _manifest(make_manifest)
    desired = _desired(manifest)
    delta = Delta(
        kind=DeltaKind.CREATE,
        object_kind=SpecKind.WORKLOAD,
        object_ref="agent-1",
        desired=desired,
    )
    diff = DiffResult(deltas=[delta])
    result = executor.execute(_action(ActionKind.REGISTER_WORKLOAD), diff)
    assert result.status is ActionStatus.APPLIED
    assert registry.get("agent-1").name == "agent-1"

    again = executor.execute(_action(ActionKind.REGISTER_WORKLOAD), diff)
    assert again.status is ActionStatus.SKIPPED


def test_update_action_applies_merged_payload(make_manifest: Any) -> None:
    registry = RegistryService(InMemoryRegistryStore())
    registry.create(_manifest(make_manifest))
    executor = ActionExecutor(registry)
    merged = _manifest(make_manifest).model_dump(by_alias=True, mode="json", exclude_none=True)
    merged["spec"]["budget"]["per_run_usd"] = 0.9
    delta = Delta(
        kind=DeltaKind.UPDATE,
        object_kind=SpecKind.WORKLOAD,
        object_ref="agent-1",
        payload=merged,
    )
    result = executor.execute(
        _action(ActionKind.UPDATE_WORKLOAD, action_class=ActionClass.SOFT),
        DiffResult(deltas=[delta]),
    )
    assert result.status is ActionStatus.APPLIED
    assert registry.get("agent-1").manifest.spec.budget.per_run_usd == 0.9


def test_recertify_action_marks_workload(make_manifest: Any) -> None:
    registry = RegistryService(InMemoryRegistryStore())
    registry.create(_manifest(make_manifest))
    executor = ActionExecutor(registry)
    delta = Delta(
        kind=DeltaKind.UPDATE, object_kind=SpecKind.WORKLOAD, object_ref="agent-1"
    )
    result = executor.execute(
        _action(ActionKind.RECERTIFY_WORKLOAD, action_class=ActionClass.SOFT),
        DiffResult(deltas=[delta]),
    )
    assert result.status is ActionStatus.APPLIED
    assert registry.get("agent-1").needs_re_certification is True


def test_deregister_action_removes_workload(make_manifest: Any) -> None:
    registry = RegistryService(InMemoryRegistryStore())
    registry.create(_manifest(make_manifest))
    executor = ActionExecutor(registry)
    result = executor.execute(
        _action(
            ActionKind.DEREGISTER_WORKLOAD,
            action_class=ActionClass.DESTRUCTIVE,
        ),
        DiffResult(),
    )
    assert result.status is ActionStatus.APPLIED
    with pytest.raises(WorkloadNotFoundError):
        registry.get("agent-1")


def test_deregister_missing_workload_is_skipped() -> None:
    registry = RegistryService(InMemoryRegistryStore())
    executor = ActionExecutor(registry)
    result = executor.execute(
        _action(ActionKind.DEREGISTER_WORKLOAD, action_class=ActionClass.DESTRUCTIVE),
        DiffResult(),
    )
    assert result.status is ActionStatus.SKIPPED


def test_quarantine_action_blocks_admission(make_manifest: Any) -> None:
    registry = RegistryService(InMemoryRegistryStore())
    registry.create(_manifest(make_manifest))
    executor = ActionExecutor(registry)
    result = executor.execute(
        _action(
            ActionKind.QUARANTINE_WORKLOAD,
            action_class=ActionClass.DESTRUCTIVE,
        ),
        DiffResult(),
    )
    assert result.status is ActionStatus.APPLIED
    assert registry.get("agent-1").certification_status is CertificationStatus.QUARANTINED


def test_blocked_action_is_not_executed(make_manifest: Any) -> None:
    registry = RegistryService(InMemoryRegistryStore())
    registry.create(_manifest(make_manifest))
    executor = ActionExecutor(registry)
    action = _action(
        ActionKind.DEREGISTER_WORKLOAD,
        action_class=ActionClass.DESTRUCTIVE,
        blocked=True,
        block_reason="not permitted",
    )
    result = executor.execute(action, DiffResult())
    assert result.status is ActionStatus.BLOCKED
    assert registry.get("agent-1").name == "agent-1"


def test_enforce_policy_without_enforcer_is_skipped() -> None:
    registry = RegistryService(InMemoryRegistryStore())
    executor = ActionExecutor(registry)
    result = executor.execute(
        _action(
            ActionKind.ENFORCE_POLICY_VERSION,
            action_class=ActionClass.SOFT,
        ),
        DiffResult(),
    )
    assert result.status is ActionStatus.SKIPPED


def test_enforce_policy_with_enforcer_applies() -> None:
    registry = RegistryService(InMemoryRegistryStore())
    seen: list[str] = []
    executor = ActionExecutor(registry, policy_enforcer=lambda spec: seen.append(spec.name))
    desired = DesiredSpec(
        spec_id="policy/platform",
        source_id="git-main",
        source=SpecSource.GIT,
        kind=SpecKind.POLICY,
        name="platform",
        revision="rev-1",
        content_hash="d" * 64,
        spec={"metadata": {"name": "platform", "version": "3"}},
        updated_at=_NOW,
    )
    delta = Delta(
        kind=DeltaKind.ENFORCE_POLICY,
        object_kind=SpecKind.POLICY,
        object_ref="platform",
        desired=desired,
    )
    result = executor.execute(
        _action(
            ActionKind.ENFORCE_POLICY_VERSION,
            ref="platform",
            action_class=ActionClass.SOFT,
        ),
        DiffResult(deltas=[delta]),
    )
    assert result.status is ActionStatus.APPLIED
    assert seen == ["platform"]


def test_action_result_round_trips() -> None:
    result = ActionResult(
        action_id="act-1",
        kind=ActionKind.REGISTER_WORKLOAD,
        object_kind=SpecKind.WORKLOAD,
        object_ref="agent-1",
        status=ActionStatus.APPLIED,
        detail="registered",
    )
    assert result.status is ActionStatus.APPLIED
