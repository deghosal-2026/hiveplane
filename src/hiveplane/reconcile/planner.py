"""Turn convergence deltas into ordered, guarded reconcile actions (M26-02/M26-05).

The planner classifies each delta (additive / soft / destructive), orders the
actions so registrations precede updates, and applies guardrails: destructive
actions are blocked unless explicitly permitted, an empty desired set cannot
cascade to mass deregistration, the first reconcile against unknown state needs
confirmation, and destructive actions are rate-limited per pass.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from datetime import datetime

from hiveplane.reconcile.differ import Delta, DeltaKind, DiffResult
from hiveplane.reconcile.models import (
    ActionClass,
    ActionKind,
    ReconcileAction,
    ReconcileMode,
    ReconcilePlan,
)

#: Deltas mapped to the action kind that executes them, with their class.
_ACTION_MAP: dict[DeltaKind, tuple[ActionKind, ActionClass]] = {
    DeltaKind.CREATE: (ActionKind.REGISTER_WORKLOAD, ActionClass.ADDITIVE),
    DeltaKind.UPDATE: (ActionKind.UPDATE_WORKLOAD, ActionClass.SOFT),
    DeltaKind.DEREGISTER: (ActionKind.DEREGISTER_WORKLOAD, ActionClass.DESTRUCTIVE),
    DeltaKind.QUARANTINE: (ActionKind.QUARANTINE_WORKLOAD, ActionClass.DESTRUCTIVE),
    DeltaKind.ENFORCE_POLICY: (ActionKind.ENFORCE_POLICY_VERSION, ActionClass.SOFT),
}

#: Order in which action kinds are planned (additive before destructive).
_ORDER: dict[ActionKind, int] = {
    ActionKind.REGISTER_WORKLOAD: 0,
    ActionKind.UPDATE_WORKLOAD: 1,
    ActionKind.RECERTIFY_WORKLOAD: 2,
    ActionKind.ENFORCE_POLICY_VERSION: 3,
    ActionKind.DEREGISTER_WORKLOAD: 4,
    ActionKind.QUARANTINE_WORKLOAD: 5,
}


@dataclass(frozen=True, slots=True)
class Guardrails:
    """Safety limits that gate destructive reconcile actions."""

    allow_destructive: bool = False
    allow_empty: bool = False
    max_destructive_per_run: int = 10
    require_destructive_confirmation: bool = True


class Planner:
    """Maps a diff into an ordered, classified, guardrailed action set."""

    def __init__(self, guardrails: Guardrails | None = None) -> None:
        self._guardrails = guardrails or Guardrails()

    def plan(
        self,
        diff: DiffResult,
        *,
        source_id: str,
        revision: str,
        mode: ReconcileMode,
        created_at: datetime,
        desired_count: int,
        first_run: bool = False,
        confirmed: bool = False,
    ) -> ReconcilePlan:
        """Return the planned action set for ``diff``."""
        actions: list[ReconcileAction] = []
        destructive_seen = 0
        for delta in diff.deltas:
            action = self._to_action(delta, source_id, revision)
            if action.destructive:
                reason = self._destructive_block(
                    desired_count, destructive_seen, first_run, confirmed
                )
                if reason is not None:
                    action = action.model_copy(update={"blocked": True, "block_reason": reason})
                else:
                    destructive_seen += 1
            actions.append(action)
            if delta.kind is DeltaKind.UPDATE and delta.recert:
                actions.append(self._recert_action(delta, source_id, revision))
        actions.sort(key=lambda action: _ORDER[action.kind])
        return ReconcilePlan(
            source_id=source_id,
            revision=revision,
            mode=mode,
            actions=actions,
            created_at=created_at,
        )

    def _destructive_block(
        self,
        desired_count: int,
        destructive_seen: int,
        first_run: bool,
        confirmed: bool,
    ) -> str | None:
        guardrails = self._guardrails
        if not guardrails.allow_destructive:
            return "destructive actions are not permitted by guardrails"
        if desired_count == 0 and not guardrails.allow_empty:
            return "empty desired set cannot cascade destructive actions"
        if first_run and guardrails.require_destructive_confirmation and not confirmed:
            return "first reconcile against unknown state requires confirmation"
        if destructive_seen >= guardrails.max_destructive_per_run:
            return (
                "destructive action rate limit reached "
                f"({guardrails.max_destructive_per_run} per reconcile)"
            )
        return None

    @staticmethod
    def _to_action(delta: Delta, source_id: str, revision: str) -> ReconcileAction:
        kind, action_class = _ACTION_MAP[delta.kind]
        return ReconcileAction(
            action_id=_action_id(source_id, revision, kind, delta.object_ref),
            kind=kind,
            object_kind=delta.object_kind,
            object_ref=delta.object_ref,
            action_class=action_class,
            summary=_summary(kind, delta.object_ref),
            details={"delta": delta.kind.value, "reason": delta.reason},
        )

    @staticmethod
    def _recert_action(delta: Delta, source_id: str, revision: str) -> ReconcileAction:
        return ReconcileAction(
            action_id=_action_id(
                source_id, revision, ActionKind.RECERTIFY_WORKLOAD, delta.object_ref
            ),
            kind=ActionKind.RECERTIFY_WORKLOAD,
            object_kind=delta.object_kind,
            object_ref=delta.object_ref,
            action_class=ActionClass.SOFT,
            summary=_summary(ActionKind.RECERTIFY_WORKLOAD, delta.object_ref),
            details={"delta": delta.kind.value},
        )


def _action_id(source_id: str, revision: str, kind: ActionKind, ref: str) -> str:
    material = f"{source_id}|{revision}|{kind.value}|{ref}".encode()
    return f"act-{hashlib.sha256(material).hexdigest()[:24]}"


def _summary(kind: ActionKind, ref: str) -> str:
    verb = {
        ActionKind.REGISTER_WORKLOAD: "register",
        ActionKind.UPDATE_WORKLOAD: "update",
        ActionKind.RECERTIFY_WORKLOAD: "re-certify",
        ActionKind.DEREGISTER_WORKLOAD: "deregister",
        ActionKind.QUARANTINE_WORKLOAD: "quarantine",
        ActionKind.ENFORCE_POLICY_VERSION: "enforce policy version for",
    }[kind]
    return f"{verb} {ref}"
