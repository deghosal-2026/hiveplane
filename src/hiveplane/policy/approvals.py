"""Human approval workflow for escalated runs (D4, DD-03)."""

from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from uuid import uuid4

from hiveplane.core.approval import ApprovalRecord, ApprovalStatus
from hiveplane.core.decision import ActionClass
from hiveplane.policy.errors import ApprovalAlreadyDecidedError, ApprovalNotFoundError
from hiveplane.policy.store import ApprovalStore


class ApprovalService:
    """Creates and resolves approval requests raised by policy escalation."""

    def __init__(
        self,
        store: ApprovalStore,
        *,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: f"ap-{uuid4().hex[:12]}")

    def request(
        self,
        *,
        run_id: str,
        workload: str,
        rule: str,
        reason: str,
        action_class: ActionClass | None = None,
    ) -> ApprovalRecord:
        """Create a pending approval request for a run."""
        record = ApprovalRecord(
            approval_id=self._id_factory(),
            run_id=run_id,
            workload=workload,
            rule=rule,
            reason=reason,
            action_class=action_class,
            requested_at=self._clock(),
        )
        self._store.save(record)
        return record

    def get(self, approval_id: str) -> ApprovalRecord:
        """Return an approval by id."""
        record = self._store.get(approval_id)
        if record is None:
            raise ApprovalNotFoundError(approval_id)
        return record

    def list(
        self, *, status: ApprovalStatus | None = None, workload: str | None = None
    ) -> list[ApprovalRecord]:
        """List approvals, optionally filtered."""
        return self._store.list_approvals(status=status, workload=workload)

    def decide(
        self,
        approval_id: str,
        *,
        status: ApprovalStatus,
        operator: str,
        reason: str | None = None,
    ) -> ApprovalRecord:
        """Approve or deny a pending request."""
        if status is ApprovalStatus.PENDING:
            raise ValueError("decision status must be approved or denied")
        record = self.get(approval_id)
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
        self._store.save(decided)
        return decided
