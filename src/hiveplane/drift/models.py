"""Drift, quarantine, and expiry models (M34)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

from hiveplane.certification.models import Severity
from hiveplane.tenancy import DEFAULT_TENANT_ID


class DriftVerdict(StrEnum):
    """The outcome of a single drift assessment."""

    STABLE = "stable"
    WARNING = "warning"
    DRIFTED = "drifted"


class QuarantineStatus(StrEnum):
    """Lifecycle of a quarantine record."""

    ACTIVE = "active"
    REINSTATED = "reinstated"


class ExpiryState(StrEnum):
    """Certification expiry state relative to a renewal window."""

    VALID = "valid"
    EXPIRING = "expiring"
    EXPIRED = "expired"
    UNKNOWN = "unknown"


class DriftAssessment(BaseModel):
    """One drift evaluation of a workload against its certified baseline.

    ``exceeded`` means the observed performance crossed a drift threshold;
    ``should_quarantine`` additionally requires either enough consecutive
    exceeding evaluations (false-positive control) or a strong signal.
    """

    model_config = ConfigDict(extra="forbid")

    workload: str = Field(min_length=1)
    baseline_attestation_id: str | None = None
    verdict: DriftVerdict
    pass_rate_before: float = Field(ge=0.0, le=1.0)
    pass_rate_after: float = Field(ge=0.0, le=1.0)
    pass_rate_delta: float
    failed_before: int = Field(ge=0)
    failed_after: int = Field(ge=0)
    new_failures: int = Field(ge=0)
    critical_failures: int = Field(ge=0)
    threshold_pass_rate: float = Field(ge=0.0, le=1.0)
    max_new_failures: int = Field(ge=0)
    required_consecutive_failures: int = Field(ge=1)
    consecutive_failures: int = Field(ge=1)
    exceeded: bool
    strong_signal: bool
    should_quarantine: bool
    reason: str = Field(min_length=1)
    evidence: dict[str, JsonValue] = Field(default_factory=dict)
    tenant_id: str = DEFAULT_TENANT_ID
    timestamp: AwareDatetime


class QuarantineRecord(BaseModel):
    """A persisted auto- or operator-quarantine with its evidence and history."""

    model_config = ConfigDict(extra="forbid")

    quarantine_id: str = Field(min_length=1)
    workload: str = Field(min_length=1)
    status: QuarantineStatus = QuarantineStatus.ACTIVE
    severity: Severity = Severity.WARNING
    reason: str = Field(min_length=1)
    actor: str = "drift-detector"
    baseline_attestation_id: str | None = None
    assessment: DriftAssessment | None = None
    evidence: dict[str, JsonValue] = Field(default_factory=dict)
    regression_diff: dict[str, JsonValue] | None = None
    notified: list[str] = Field(default_factory=list)
    cancelled_runs: list[str] = Field(default_factory=list)
    tenant_id: str = DEFAULT_TENANT_ID
    timestamp: AwareDatetime
    reinstated_at: AwareDatetime | None = None
    reinstated_by: str | None = None


class DriftSchedule(BaseModel):
    """A workload's periodic re-certification schedule (M34-01)."""

    model_config = ConfigDict(extra="forbid")

    schedule_id: str = Field(min_length=1)
    workload: str = Field(min_length=1)
    interval_seconds: int = Field(ge=1)
    last_certified_at: AwareDatetime | None = None
    next_re_cert_run: AwareDatetime
    expires_at: AwareDatetime | None = None
    status: str = "scheduled"
    tenant_id: str = DEFAULT_TENANT_ID


class DueWorkload(BaseModel):
    """A workload whose re-certification window is due or overdue."""

    model_config = ConfigDict(extra="forbid")

    workload: str = Field(min_length=1)
    next_re_cert_run: AwareDatetime
    expires_at: AwareDatetime | None = None
    overdue: bool = True
    expiry_state: ExpiryState = ExpiryState.UNKNOWN


class CertificationExpiry(BaseModel):
    """Certification expiry/renewal status for a workload (M34-07)."""

    model_config = ConfigDict(extra="forbid")

    workload: str = Field(min_length=1)
    state: ExpiryState
    expires_at: AwareDatetime | None = None
    days_remaining: float | None = None
    renewable: bool = True
