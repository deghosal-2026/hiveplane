"""Fan-out, interactive approval, escalation, and preference models (M51)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from hiveplane.tenancy.context import DEFAULT_TENANT_ID


class DeliveryError(Exception):
    """Base class for delivery failures."""


class AlreadyResolvedError(DeliveryError):
    """Raised when a resolved approval is replayed (single-use)."""

    def __init__(self, approval_id: str) -> None:
        super().__init__(f"approval {approval_id!r} is already resolved")
        self.approval_id = approval_id


class InvalidApprovalTokenError(DeliveryError):
    """Raised when an interactive/mobile approval token is invalid or expired."""


class DeliveryChannel(StrEnum):
    """The nine supported delivery channels (M51-01)."""

    SLACK = "slack"
    TEAMS = "teams"
    JIRA = "jira"
    GITHUB_PR = "github_pr"
    PAGERDUTY = "pagerduty"
    DISCORD = "discord"
    LINEAR = "linear"
    EMAIL = "email"
    WEBHOOK = "webhook"


class DeliveryEventType(StrEnum):
    """The event types a delivery can carry."""

    COMPLETED = "completed"
    FAILED = "failed"
    ESCALATION = "escalation"
    APPROVAL_REQUESTED = "approval_requested"
    APPROVAL_RESOLVED = "approval_resolved"
    BUDGET_ALERT = "budget_alert"


class DeliveryStatus(StrEnum):
    """The disposition of one delivery attempt."""

    DELIVERED = "delivered"
    FAILED = "failed"
    SUPPRESSED = "suppressed"
    BATCHED = "batched"
    DEAD_LETTER = "dead_letter"


class DeliveryEnvelope(BaseModel):
    """The common message envelope rendered by every channel adapter (M51-02)."""

    model_config = ConfigDict(extra="forbid")

    event_type: DeliveryEventType
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    team_id: str | None = None
    workload: str | None = None
    run_id: str | None = None
    approval_id: str | None = None
    status: str | None = None
    summary: str = ""
    trace_link: str | None = None
    attestation_link: str | None = None
    public_verify_link: str | None = None
    artifact_links: list[str] = Field(default_factory=list)


class DeliveryDestination(BaseModel):
    """A resolved delivery target on a channel."""

    model_config = ConfigDict(extra="forbid")

    channel: DeliveryChannel
    target: str = Field(min_length=1, max_length=2000)


class DeliveryAttempt(BaseModel):
    """An audited delivery: destination, attempts, status, error (M51-07)."""

    model_config = ConfigDict(extra="forbid")

    attempt_id: str = Field(min_length=1)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    event_type: DeliveryEventType
    channel: DeliveryChannel
    target: str = Field(min_length=1, max_length=2000)
    status: DeliveryStatus
    attempts: int = Field(default=1, ge=0)
    run_id: str | None = None
    approval_id: str | None = None
    error: str | None = None
    created_at: AwareDatetime
    delivered_at: AwareDatetime | None = None


class NotificationPreference(BaseModel):
    """Per-team routing, batching, and quiet hours (M51-06)."""

    model_config = ConfigDict(extra="forbid")

    team_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    events: list[DeliveryEventType] = Field(default_factory=list)
    channels: list[DeliveryChannel] = Field(default_factory=list)
    destinations: list[DeliveryDestination] = Field(default_factory=list)
    batch_window_s: int = Field(default=0, ge=0)
    quiet_hours_start: int | None = Field(default=None, ge=0, le=23)
    quiet_hours_end: int | None = Field(default=None, ge=0, le=23)
    critical_bypasses_quiet: bool = True


class ApprovalDecisionRecord(BaseModel):
    """An attributed interactive/mobile approval decision (M51-03/04)."""

    model_config = ConfigDict(extra="forbid")

    approval_id: str = Field(min_length=1)
    operator_id: str = Field(min_length=1)
    decision: str = Field(pattern="^(approve|deny)$")
    channel: str = Field(default="api")
    reason: str | None = None
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    decided_at: AwareDatetime


class EscalationRecord(BaseModel):
    """An escalation of an unanswered approval to the next operator (M51-05)."""

    model_config = ConfigDict(extra="forbid")

    approval_id: str = Field(min_length=1)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    level: int = Field(ge=1)
    target: str = Field(min_length=1, max_length=253)
    fired_at: AwareDatetime
    responded_at: AwareDatetime | None = None
