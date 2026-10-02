"""Scheduler models: QoS, queue entries, admission, preemption (M47)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class QosClass(StrEnum):
    """Quality-of-service class controlling scheduling and preemption."""

    GUARANTEED = "guaranteed"
    BURSTABLE = "burstable"
    BEST_EFFORT = "best_effort"

    @property
    def rank(self) -> int:
        """Higher rank schedules first."""
        return _QOS_RANK[self]

    @property
    def preemptible(self) -> bool:
        """Guaranteed work is never preempted; the rest is."""
        return self is not QosClass.GUARANTEED


_QOS_RANK: dict[QosClass, int] = {
    QosClass.GUARANTEED: 2,
    QosClass.BURSTABLE: 1,
    QosClass.BEST_EFFORT: 0,
}


class BackpressureReason(StrEnum):
    """Why admission refused or deferred work, mapped to a stable code."""

    PER_TENANT_LIMIT = "capacity: per_tenant_limit"
    PER_WORKLOAD_LIMIT = "capacity: per_workload_limit"
    QUEUE_DEPTH = "capacity: queue_depth"
    MAINTENANCE = "maintenance: frozen"


class AdmissionAction(StrEnum):
    """The disposition of a submitted task."""

    QUEUED = "queued"
    REJECTED = "rejected"


class QueueEntry(BaseModel):
    """A task waiting for capacity."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(min_length=1, max_length=64)
    workload: str = Field(min_length=1, max_length=253)
    team: str | None = Field(default=None, max_length=253)
    run_id: str | None = None
    qos: QosClass = QosClass.BEST_EFFORT
    priority: int = Field(default=0)
    attempts: int = Field(default=1, ge=1)
    preempted_by: str | None = None
    submitted_at: AwareDatetime


class AdmissionDecision(BaseModel):
    """The outcome of submitting a task: queued, or rejected with a reason."""

    model_config = ConfigDict(extra="forbid")

    entry: QueueEntry
    action: AdmissionAction
    reason: BackpressureReason | None = None

    @property
    def code(self) -> str | None:
        """Return the stable reason code, if rejected."""
        return None if self.reason is None else self.reason.value


class PreemptionRecord(BaseModel):
    """Attribution for a checkpoint-safe preemption (M47-05)."""

    model_config = ConfigDict(extra="forbid")

    victim_task_id: str = Field(min_length=1)
    victim_workload: str = Field(min_length=1)
    victim_qos: QosClass
    preempted_by: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    at: AwareDatetime


class WaitingReason(BaseModel):
    """A queued task and why it is still waiting (queue visualizer, M47-08)."""

    model_config = ConfigDict(extra="forbid")

    task_id: str = Field(min_length=1)
    workload: str = Field(min_length=1)
    qos: QosClass
    priority: int
    reason: str = Field(min_length=1)


class QueueSnapshot(BaseModel):
    """A point-in-time view of queue depth, priorities, and running load."""

    model_config = ConfigDict(extra="forbid")

    depth: int = Field(ge=0)
    by_qos: dict[str, int] = Field(default_factory=dict)
    by_priority: dict[str, int] = Field(default_factory=dict)
    waiting: list[WaitingReason] = Field(default_factory=list)
    running_by_workload: dict[str, int] = Field(default_factory=dict)
    running_by_tenant: dict[str, int] = Field(default_factory=dict)
