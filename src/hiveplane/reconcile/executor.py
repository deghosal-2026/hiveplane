"""Execute planned reconcile actions through existing service APIs (M26-03).

The executor never writes tables directly: registrations, updates, re-cert
requests, deregistrations, and quarantines all go through
:class:`~hiveplane.registry.service.RegistryService`, so reconcile cannot bypass
certification, policy, or budget enforcement. Every action yields an
:class:`~hiveplane.reconcile.models.ActionResult`; one failure never aborts the
rest of the plan.
"""

from __future__ import annotations

from collections.abc import Callable

from hiveplane.core.manifest import parse_manifest
from hiveplane.fleet.reconcile import DesiredSpec
from hiveplane.reconcile.differ import Delta, DeltaKind, DiffResult
from hiveplane.reconcile.models import (
    ActionKind,
    ActionResult,
    ActionStatus,
    ReconcileAction,
)
from hiveplane.registry.errors import WorkloadAlreadyExistsError, WorkloadNotFoundError
from hiveplane.registry.service import RegistryService
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext

#: Maps an action kind onto the delta kind that carries its desired payload.
_DELTA_FOR: dict[ActionKind, DeltaKind] = {
    ActionKind.REGISTER_WORKLOAD: DeltaKind.CREATE,
    ActionKind.UPDATE_WORKLOAD: DeltaKind.UPDATE,
    ActionKind.RECERTIFY_WORKLOAD: DeltaKind.UPDATE,
    ActionKind.ENFORCE_POLICY_VERSION: DeltaKind.ENFORCE_POLICY,
    ActionKind.DEREGISTER_WORKLOAD: DeltaKind.DEREGISTER,
    ActionKind.QUARANTINE_WORKLOAD: DeltaKind.QUARANTINE,
}


class ActionExecutor:
    """Applies reconcile actions to the control plane."""

    def __init__(
        self,
        registry: RegistryService,
        *,
        policy_enforcer: Callable[[DesiredSpec], None] | None = None,
    ) -> None:
        self._registry = registry
        self._policy_enforcer = policy_enforcer

    def execute(
        self,
        action: ReconcileAction,
        diff: DiffResult,
        *,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> ActionResult:
        """Execute one action for ``ctx``'s tenant and return its recorded result."""
        if action.blocked:
            return _result(action, ActionStatus.BLOCKED, action.block_reason)
        delta = _find_delta(action, diff)
        try:
            return self._dispatch(action, delta, ctx)
        except WorkloadNotFoundError as exc:
            return _result(action, ActionStatus.FAILED, str(exc))

    def _dispatch(
        self, action: ReconcileAction, delta: Delta | None, ctx: TenantContext
    ) -> ActionResult:
        match action.kind:
            case ActionKind.REGISTER_WORKLOAD:
                return self._do_register_workload(action, delta, ctx)
            case ActionKind.UPDATE_WORKLOAD:
                return self._do_update_workload(action, delta, ctx)
            case ActionKind.RECERTIFY_WORKLOAD:
                return self._do_recertify_workload(action, delta, ctx)
            case ActionKind.DEREGISTER_WORKLOAD:
                return self._do_deregister_workload(action, delta, ctx)
            case ActionKind.QUARANTINE_WORKLOAD:
                return self._do_quarantine_workload(action, delta, ctx)
            case ActionKind.ENFORCE_POLICY_VERSION:
                return self._do_enforce_policy_version(action, delta, ctx)

    # ------------------------------------------------------------------ #
    # Action handlers
    # ------------------------------------------------------------------ #
    def _do_register_workload(
        self, action: ReconcileAction, delta: Delta | None, ctx: TenantContext
    ) -> ActionResult:
        desired = _require_desired(action, delta)
        manifest = parse_manifest(desired.spec)
        try:
            self._registry.create(manifest, ctx=ctx)
        except WorkloadAlreadyExistsError:
            return _result(action, ActionStatus.SKIPPED, "already registered")
        return _result(action, ActionStatus.APPLIED, "registered")

    def _do_update_workload(
        self, action: ReconcileAction, delta: Delta | None, ctx: TenantContext
    ) -> ActionResult:
        if delta is None or delta.payload is None:
            return _result(action, ActionStatus.FAILED, "no merged payload to apply")
        manifest = parse_manifest(delta.payload)
        self._registry.update(action.object_ref, manifest, ctx=ctx)
        return _result(action, ActionStatus.APPLIED, "manifest updated")

    def _do_recertify_workload(
        self, action: ReconcileAction, delta: Delta | None, ctx: TenantContext
    ) -> ActionResult:
        self._registry.request_re_certification(action.object_ref, ctx=ctx)
        return _result(action, ActionStatus.APPLIED, "re-certification requested")

    def _do_deregister_workload(
        self, action: ReconcileAction, delta: Delta | None, ctx: TenantContext
    ) -> ActionResult:
        try:
            self._registry.delete(action.object_ref, ctx=ctx)
        except WorkloadNotFoundError:
            return _result(action, ActionStatus.SKIPPED, "already absent")
        return _result(action, ActionStatus.APPLIED, "deregistered; history retained")

    def _do_quarantine_workload(
        self, action: ReconcileAction, delta: Delta | None, ctx: TenantContext
    ) -> ActionResult:
        self._registry.quarantine(action.object_ref, ctx=ctx)
        return _result(action, ActionStatus.APPLIED, "quarantined")

    def _do_enforce_policy_version(
        self, action: ReconcileAction, delta: Delta | None, ctx: TenantContext
    ) -> ActionResult:
        if self._policy_enforcer is None or delta is None or delta.desired is None:
            return _result(
                action, ActionStatus.SKIPPED, "policy enforcement is not configured"
            )
        self._policy_enforcer(delta.desired)
        return _result(action, ActionStatus.APPLIED, "policy version enforced")


def _find_delta(action: ReconcileAction, diff: DiffResult) -> Delta | None:
    wanted = _DELTA_FOR.get(action.kind)
    for delta in diff.deltas:
        if delta.kind is wanted and delta.object_ref == action.object_ref:
            return delta
    return None


def _require_desired(action: ReconcileAction, delta: Delta | None) -> DesiredSpec:
    if delta is None or delta.desired is None:
        raise WorkloadNotFoundError(f"{action.object_ref} (no desired spec)")
    return delta.desired


def _result(
    action: ReconcileAction, status: ActionStatus, detail: str | None
) -> ActionResult:
    return ActionResult(
        action_id=action.action_id,
        kind=action.kind,
        object_kind=action.object_kind,
        object_ref=action.object_ref,
        status=status,
        detail=detail,
    )
