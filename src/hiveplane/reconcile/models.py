"""Domain models for the desired-state reconciliation controller (M26).

The controller observes actual fleet state, diffs it against a validated
:class:`~hiveplane.reconcile.models.DesiredSet`, plans ordered actions, executes
them through existing service APIs, and records the result. These models are the
typed surface for that pipeline: the loaded desired set, the plan, the per-action
outcome, and the reconcile run history.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

from hiveplane.fleet.reconcile import (
    DesiredSpec,
    ReconcileStatus,
    SpecKind,
    SpecSource,
)
from hiveplane.tenancy.context import DEFAULT_TENANT_ID


class ReconcileMode(StrEnum):
    """Whether a reconcile pass only plans or also applies."""

    PLAN = "plan"
    APPLY = "apply"


class ReconcileOutcome(StrEnum):
    """The terminal disposition of a reconcile run."""

    IN_SYNC = "in_sync"
    PLANNED = "planned"
    APPLIED = "applied"
    BLOCKED = "blocked"
    ERROR = "error"


class ActionKind(StrEnum):
    """The reconcile actions the controller can plan and execute."""

    REGISTER_WORKLOAD = "register_workload"
    UPDATE_WORKLOAD = "update_workload"
    RECERTIFY_WORKLOAD = "recertify_workload"
    DEREGISTER_WORKLOAD = "deregister_workload"
    QUARANTINE_WORKLOAD = "quarantine_workload"
    ENFORCE_POLICY_VERSION = "enforce_policy_version"


class ActionClass(StrEnum):
    """How dangerous an action is, driving the guardrail gate."""

    ADDITIVE = "additive"
    SOFT = "soft"
    DESTRUCTIVE = "destructive"


class ActionStatus(StrEnum):
    """The result of executing one planned action."""

    PLANNED = "planned"
    APPLIED = "applied"
    SKIPPED = "skipped"
    BLOCKED = "blocked"
    FAILED = "failed"


class DesiredSet(BaseModel):
    """A validated, atomic set of declared fleet objects at one revision."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=128)
    source: SpecSource
    revision: str = Field(min_length=1, max_length=128)
    specs: list[DesiredSpec] = Field(default_factory=list)
    generated_at: AwareDatetime

    def get(self, kind: SpecKind, name: str) -> DesiredSpec | None:
        """Return the declared object of ``kind`` named ``name``, if any."""
        for spec in self.specs:
            if spec.kind is kind and spec.name == name:
                return spec
        return None

    def names(self, kind: SpecKind) -> list[str]:
        """Return the declared names of ``kind``, in declaration order."""
        return [spec.name for spec in self.specs if spec.kind is kind]


class ReconcileAction(BaseModel):
    """One ordered, classified change the controller intends to make."""

    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(min_length=1, max_length=128)
    kind: ActionKind
    object_kind: SpecKind
    object_ref: str = Field(min_length=1, max_length=253)
    action_class: ActionClass
    summary: str = Field(min_length=1, max_length=2000)
    blocked: bool = False
    block_reason: str | None = Field(default=None, max_length=2000)
    details: dict[str, JsonValue] = Field(default_factory=dict)

    @property
    def destructive(self) -> bool:
        """Return True when this action can remove or disable fleet objects."""
        return self.action_class is ActionClass.DESTRUCTIVE


class ActionResult(BaseModel):
    """The recorded outcome of executing (or skipping) a planned action."""

    model_config = ConfigDict(extra="forbid")

    action_id: str = Field(min_length=1, max_length=128)
    kind: ActionKind
    object_kind: SpecKind
    object_ref: str = Field(min_length=1, max_length=253)
    status: ActionStatus
    detail: str | None = Field(default=None, max_length=2000)


class ReconcilePlan(BaseModel):
    """The ordered action set produced by diffing desired against observed."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=128)
    revision: str = Field(min_length=1, max_length=128)
    mode: ReconcileMode
    actions: list[ReconcileAction] = Field(default_factory=list)
    created_at: AwareDatetime

    @property
    def destructive_actions(self) -> list[ReconcileAction]:
        """Return the destructive actions in the plan."""
        return [action for action in self.actions if action.destructive]

    @property
    def in_sync(self) -> bool:
        """Return True when no action is required."""
        return not self.actions


class ReconcileRun(BaseModel):
    """An append-only record of one reconcile pass over a source."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    source_id: str = Field(min_length=1, max_length=128)
    source: SpecSource
    revision: str = Field(min_length=1, max_length=128)
    mode: ReconcileMode
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None
    outcome: ReconcileOutcome
    actions: list[ActionResult] = Field(default_factory=list)
    error: str | None = Field(default=None, max_length=4000)

    @property
    def action_count(self) -> int:
        """Return the number of actions considered in this run."""
        return len(self.actions)

    def _count(self, status: ActionStatus) -> int:
        return sum(1 for action in self.actions if action.status is status)

    @property
    def applied_count(self) -> int:
        """Return the number of actions applied."""
        return self._count(ActionStatus.APPLIED)

    @property
    def blocked_count(self) -> int:
        """Return the number of actions blocked by guardrails."""
        return self._count(ActionStatus.BLOCKED)

    @property
    def failed_count(self) -> int:
        """Return the number of actions that failed."""
        return self._count(ActionStatus.FAILED)

    @property
    def skipped_count(self) -> int:
        """Return the number of actions skipped as no-ops."""
        return self._count(ActionStatus.SKIPPED)


class ReconcileStatusView(BaseModel):
    """A source's convergence status, surfaced via API and CLI."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=128)
    source: SpecSource
    status: ReconcileStatus
    last_revision: str | None = Field(default=None, max_length=128)
    last_reconcile_at: AwareDatetime | None = None
    open_drift: int = Field(default=0, ge=0)
    last_run_id: str | None = Field(default=None, max_length=128)
    last_outcome: ReconcileOutcome | None = None
