"""Approval records for policy escalations (D4)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from hiveplane.core.decision import ActionClass
from hiveplane.tenancy.context import DEFAULT_TENANT_ID


class ApprovalStatus(StrEnum):
    """State of a pending approval request."""

    PENDING = "pending"
    APPROVED = "approved"
    DENIED = "denied"


class ApprovalRecord(BaseModel):
    """An approval request raised when policy escalates a run."""

    model_config = ConfigDict(extra="forbid")

    approval_id: str = Field(min_length=1)
    run_id: str = Field(min_length=1)
    workload: str = Field(min_length=1)
    rule: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    action_class: ActionClass | None = None
    requested_at: AwareDatetime
    status: ApprovalStatus = ApprovalStatus.PENDING
    decided_at: AwareDatetime | None = None
    decided_by: str | None = None
    decision_reason: str | None = None
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
