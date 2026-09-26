"""Smart task router models: decisions, candidates, and refusals (M30-01..03)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from hiveplane.core.run import AdmissionContext
from hiveplane.tenancy.context import DEFAULT_TENANT_ID


class RouteOutcome(StrEnum):
    """Whether the router chose a target or refused."""

    ROUTED = "routed"
    REFUSED = "refused"


class RefusalReason(StrEnum):
    """Why the router refused to pick a target."""

    NO_CANDIDATES = "no_certified_candidates"
    LOW_CONFIDENCE = "low_confidence"
    AMBIGUOUS = "ambiguous"


class RouterCandidate(BaseModel):
    """One certified workload considered for a task, with its score."""

    model_config = ConfigDict(extra="forbid")

    workload: str = Field(min_length=1)
    score: float = Field(ge=0.0, le=1.0)
    rank: int = Field(ge=1)
    certified: bool = True


class RouteDecision(BaseModel):
    """The recorded outcome of routing one task (M30-03).

    The raw task text is never stored; ``task_hash`` is a stable digest used to
    correlate decisions without retaining potentially sensitive input.
    """

    model_config = ConfigDict(extra="forbid")

    decision_id: str = Field(min_length=1)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    task_hash: str = Field(min_length=1)
    context: AdmissionContext
    classifier_model: str = Field(min_length=1)
    candidates: list[RouterCandidate] = Field(default_factory=list)
    chosen: str | None = None
    outcome: RouteOutcome
    reason: RefusalReason | None = None
    confidence_threshold: float = Field(ge=0.0, le=1.0)
    margin: float = Field(ge=0.0, le=1.0)
    created_at: AwareDatetime

    @property
    def routed(self) -> bool:
        """Whether a target workload was chosen."""
        return self.outcome is RouteOutcome.ROUTED


class RouteRequest(BaseModel):
    """An API request to route a plain-language task."""

    model_config = ConfigDict(extra="forbid")

    task: str = Field(min_length=1)
    context: AdmissionContext = AdmissionContext.STAGING


class RouterNotConfiguredError(Exception):
    """Raised when the router has no usable classifier."""

    def __init__(self, reason: str) -> None:
        super().__init__(f"router is not configured: {reason}")
        self.reason = reason
