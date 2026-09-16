"""Policy decision models (DD-03)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from hiveplane.certification.models import CertificationStatus
from hiveplane.core.run import AdmissionContext
from hiveplane.core.tools import ToolTrustLevel


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
