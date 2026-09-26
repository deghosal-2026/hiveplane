"""Policy decision models (DD-03)."""

from __future__ import annotations

from datetime import datetime
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from hiveplane.certification.models import CertificationStatus
from hiveplane.core.run import AdmissionContext
from hiveplane.core.tools import ToolsSpec, ToolTrustLevel


class DecisionOutcome(StrEnum):
    """The outcome of evaluating policy for a run or tool call."""

    ALLOW = "allow"
    DENY = "deny"
    ESCALATE = "escalate"
    BLOCK_INJECTION = "block_injection"


class DataSensitivity(StrEnum):
    """Sensitivity classification of the data a run touches."""

    PUBLIC = "public"
    INTERNAL = "internal"
    PII = "pii"
    RESTRICTED = "restricted"


class ActionClass(StrEnum):
    """Known action classes used by policy and approvals."""

    READ_ONLY = "read_only"
    DESTRUCTIVE = "destructive"
    PRODUCTION_WRITE = "production_write"
    HIGH_SPEND = "high_spend"


class BlastRadius(BaseModel):
    """A blast-radius score (0-100) and the factor contributions to it."""

    model_config = ConfigDict(extra="forbid")

    score: int = Field(ge=0, le=100)
    factors: dict[str, int] = Field(default_factory=dict)


class TimeWindow(BaseModel):
    """A business-hours allow window or a blackout calendar for actions (M40-05).

    ``blackout`` windows deny every matched action; otherwise an action matching
    ``action_class``/``tool_trust`` is denied outside ``[start, end)`` or on a day
    not listed in ``days`` (0=Monday..6=Sunday; empty means any day).
    """

    model_config = ConfigDict(extra="forbid")

    action_class: ActionClass | None = None
    tool_trust: ToolTrustLevel | None = None
    days: list[int] = Field(default_factory=list)
    start: str = Field(default="00:00", pattern=r"^\d{2}:\d{2}$")
    end: str = Field(default="24:00", pattern=r"^\d{2}:\d{2}$")
    tz: str = "UTC"
    blackout: bool = False

    def matches(self, context: PolicyContext) -> bool:
        """Return True when this window applies to the context's action."""
        if self.action_class is not None and self.action_class is not context.action_class:
            return False
        return not (
            self.tool_trust is not None and self.tool_trust is not context.tool_trust
        )

    def allows(self, when: datetime) -> bool:
        """Return True when ``when`` falls inside the window's allowed period."""
        from zoneinfo import ZoneInfo

        local = when.astimezone(ZoneInfo(self.tz))
        if self.days and local.weekday() not in self.days:
            return False
        start_hour, start_minute = (int(part) for part in self.start.split(":"))
        end_hour, end_minute = (int(part) for part in self.end.split(":"))
        minutes = local.hour * 60 + local.minute
        return start_hour * 60 + start_minute <= minutes < end_hour * 60 + end_minute


class PolicyContext(BaseModel):
    """The context a policy decision is evaluated against."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    workload: str = Field(min_length=1)
    team: str | None = None
    environment: AdmissionContext
    action_class: ActionClass | None = None
    tool_id: str | None = None
    tool_trust: ToolTrustLevel | None = None
    data_sensitivity: DataSensitivity = DataSensitivity.INTERNAL
    certification_status: CertificationStatus = CertificationStatus.UNCERTIFIED
    tools: ToolsSpec | None = None
    approval_required_for: list[ActionClass] = Field(default_factory=list)
    injection_detected: bool = False
    budget_exhausted: bool = False
    taint_untrusted: bool = False
    at: AwareDatetime | None = None
    time_windows: list[TimeWindow] = Field(default_factory=list)


class PolicyDecision(BaseModel):
    """A policy evaluation with its reason and the rule that produced it."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    outcome: DecisionOutcome
    reason: str = Field(min_length=1)
    rule: str = Field(min_length=1)
    action_class: ActionClass | None = None
    timestamp: AwareDatetime
    blast_radius: BlastRadius | None = None
    certification_status: CertificationStatus | None = None
    pack_version: str | None = None
    detector_set_version: str | None = None
    dry_run: bool = False
