"""Execution domain models for the run lifecycle."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

from hiveplane.core.fanout import FanOutType
from hiveplane.core.run import AdmissionContext, Run
from hiveplane.core.workload import AgentWorkload


class AdmissionOutcome(StrEnum):
    """The disposition of an admission decision."""

    ADMITTED = "admitted"
    SANDBOX_ONLY = "sandbox_only"
    REFUSED = "refused"


class AdmissionCheck(BaseModel):
    """One step of the admission pipeline with its outcome."""

    model_config = ConfigDict(extra="forbid")

    step: str = Field(min_length=1)
    passed: bool
    reason: str | None = None
    rule: str | None = None


class AdmissionResult(BaseModel):
    """The full admission decision for a run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    workload: str = Field(min_length=1)
    context: AdmissionContext
    outcome: AdmissionOutcome
    checks: list[AdmissionCheck] = Field(default_factory=list)
    sandbox: bool = False
    escalation_required: bool = False
    refused_reason: str | None = None

    def policy_check(self) -> AdmissionCheck | None:
        """Return the policy step check, if present."""
        for check in self.checks:
            if check.step == "policy":
                return check
        return None


class InterventionAction(StrEnum):
    """Operator interventions on a live run."""

    PAUSE = "pause"
    RESUME = "resume"
    STOP = "stop"


class DeliveryStatus(StrEnum):
    """Delivery state of a fan-out message."""

    PENDING = "pending"
    DELIVERED = "delivered"
    FAILED = "failed"


class DeliveryRecord(BaseModel):
    """A recorded attempt to deliver a run result to a destination."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    destination_type: FanOutType
    target: str = Field(min_length=1)
    status: DeliveryStatus
    attempts: int = Field(ge=0)
    error: str | None = None
    timestamp: AwareDatetime


class RunContext(BaseModel):
    """The execution context handed to a RunExecutor."""

    model_config = ConfigDict(extra="forbid")

    run: Run
    workload: AgentWorkload
    sandbox: bool = False


class RunSubmission(BaseModel):
    """The request body for submitting a run."""

    model_config = ConfigDict(extra="forbid")

    workload: str = Field(min_length=1)
    caller: str = Field(min_length=1)
    context: AdmissionContext
    task: dict[str, JsonValue] = Field(default_factory=dict)
    model_identity: str | None = None
