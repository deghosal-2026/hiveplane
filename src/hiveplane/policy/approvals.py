"""Human approval workflow for escalated runs (D4, DD-03)."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from hiveplane import telemetry
from hiveplane.core.approval import ApprovalComment, ApprovalRecord, ApprovalStatus
from hiveplane.core.decision import ActionClass
from hiveplane.events.models import FleetEvent, FleetEventKind
from hiveplane.policy.errors import ApprovalAlreadyDecidedError, ApprovalNotFoundError
from hiveplane.policy.store import ApprovalStore
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext
from hiveplane.tenancy.context import context_for_run


class ApprovalService:
    """Creates and resolves approval requests raised by policy escalation."""

    def __init__(
        self,
        store: ApprovalStore,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
        event_sink: Callable[[FleetEvent], None] | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: f"ap-{uuid4().hex[:12]}")
        self._event_sink = event_sink
        self._escalation_register: Callable[[str, str], None] | None = None
        self._escalation_respond: Callable[[str], None] | None = None

    def bind_event_sink(self, sink: Callable[[FleetEvent], None] | None) -> None:
        """Bind the fleet-events sink (best-effort)."""
        self._event_sink = sink

    def bind_escalation(
        self,
        register: Callable[[str, str], None],
        respond: Callable[[str], None],
    ) -> None:
        """Bind on-call escalation so pending requests page and decisions clear (M51-05)."""
        self._escalation_register = register
        self._escalation_respond = respond

    def _emit(self, event_type: str, record: ApprovalRecord) -> None:
        if self._event_sink is None:
            return
        event = FleetEvent(
            kind=FleetEventKind.APPROVAL,
            event_type=event_type,
            tenant_id=record.tenant_id,
            payload={
                "approval_id": record.approval_id,
                "run_id": record.run_id,
                "workload": record.workload,
                "status": record.status.value,
            },
            occurred_at=self._clock(),
        )
        try:
            self._event_sink(event)
        except Exception:
            return

    def request(
        self,
        *,
        run_id: str,
        workload: str,
        rule: str,
        reason: str,
        action_class: ActionClass | None = None,
        tool_id: str | None = None,
        tenant_id: str | None = None,
        ctx: TenantContext | None = None,
    ) -> ApprovalRecord:
        """Create a pending approval request for a run.

        The approval is stored under the run's tenant so the owning tenant can
        see and decide it. ``ctx`` (a trusted run context) wins over the
        ``tenant_id`` convenience argument; absent both, the default tenant is
        used for backward compatibility.
        """
        scope = ctx or (context_for_run(tenant_id) if tenant_id else DEFAULT_CONTEXT)
        with telemetry.span(
            "approval",
            attributes={
                "run_id": run_id,
                "workload": workload,
                "rule": rule,
                "operation": "request",
            },
        ) as active:
            record = ApprovalRecord(
                approval_id=self._id_factory(),
                run_id=run_id,
                workload=workload,
                rule=rule,
                reason=reason,
                action_class=action_class,
                tool_id=tool_id,
                requested_at=self._clock(),
                tenant_id=scope.tenant_id,
            )
            self._store.save(record, ctx=scope)
            active.set_attribute("approval_id", record.approval_id)
            self._emit("approval.requested", record)
            if self._escalation_register is not None:
                with contextlib.suppress(Exception):
                    self._escalation_register(record.approval_id, record.tenant_id)
            return record

    def get(
        self, approval_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> ApprovalRecord:
        """Return an approval by id within the acting tenant."""
        record = self._store.get(approval_id, ctx=ctx)
        if record is None:
            raise ApprovalNotFoundError(approval_id)
        return record

    def list(
        self,
        *,
        status: ApprovalStatus | None = None,
        workload: str | None = None,
        run_id: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> list[ApprovalRecord]:
        """List approvals within the acting tenant, optionally filtered."""
        return self._store.list_approvals(
            status=status, workload=workload, run_id=run_id, ctx=ctx
        )

    def comment(
        self,
        approval_id: str,
        *,
        author: str,
        text: str,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> ApprovalRecord:
        """Append a comment to an approval request and return the updated record."""
        record = self.get(approval_id, ctx=ctx)
        with telemetry.span(
            "approval",
            attributes={
                "run_id": record.run_id,
                "workload": record.workload,
                "rule": record.rule,
                "operation": "comment",
            },
        ) as active:
            updated = record.model_copy(
                update={
                    "comments": [
                        *record.comments,
                        ApprovalComment(author=author, text=text, created_at=self._clock()),
                    ]
                }
            )
            self._store.save(updated, ctx=ctx)
            active.set_attribute("approval_id", updated.approval_id)
            active.set_attribute("author", author)
            return updated

    def delegate(
        self,
        approval_id: str,
        *,
        assignee: str,
        operator: str,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> ApprovalRecord:
        """Reassign an approval request and record who delegated it."""
        record = self.get(approval_id, ctx=ctx)
        with telemetry.span(
            "approval",
            attributes={
                "run_id": record.run_id,
                "workload": record.workload,
                "rule": record.rule,
                "operation": "delegate",
            },
        ) as active:
            updated = record.model_copy(
                update={
                    "delegated_to": assignee,
                    "delegated_by": operator,
                    "comments": [
                        *record.comments,
                        ApprovalComment(
                            author=operator,
                            text=f"delegated to {assignee}",
                            created_at=self._clock(),
                        ),
                    ],
                }
            )
            self._store.save(updated, ctx=ctx)
            active.set_attribute("approval_id", updated.approval_id)
            active.set_attribute("assignee", assignee)
            active.set_attribute("operator", operator)
            return updated

    def decide(
        self,
        approval_id: str,
        *,
        status: ApprovalStatus,
        operator: str,
        reason: str | None = None,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> ApprovalRecord:
        """Approve or deny a pending request."""
        if status is ApprovalStatus.PENDING:
            raise ValueError("decision status must be approved or denied")
        record = self.get(approval_id, ctx=ctx)
        with telemetry.span(
            "approval",
            attributes={
                "run_id": record.run_id,
                "workload": record.workload,
                "rule": record.rule,
                "operation": "decision",
            },
        ) as active:
            if record.status is not ApprovalStatus.PENDING:
                raise ApprovalAlreadyDecidedError(approval_id)
            decided = record.model_copy(
                update={
                    "status": status,
                    "decided_at": self._clock(),
                    "decided_by": operator,
                    "decision_reason": reason,
                }
            )
            self._store.save(decided, ctx=ctx)
            active.set_attribute("approval_id", decided.approval_id)
            active.set_attribute("status", status.value)
            active.set_attribute("operator", operator)
            self._emit("approval.resolved", decided)
            if self._escalation_respond is not None:
                with contextlib.suppress(Exception):
                    self._escalation_respond(decided.approval_id)
            return decided
