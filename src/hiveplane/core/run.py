"""Run lifecycle state and the Run aggregate (D2)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

from hiveplane.tenancy.context import DEFAULT_TENANT_ID


class RunState(StrEnum):
    """Observable states of a run."""

    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class AdmissionContext(StrEnum):
    """Target contexts a run can be admitted to."""

    SANDBOX = "sandbox"
    STAGING = "staging"
    PRODUCTION = "production"


_RUN_TRANSITIONS: dict[RunState, set[RunState]] = {
    RunState.QUEUED: {RunState.RUNNING, RunState.CANCELLED},
    RunState.RUNNING: {
        RunState.PAUSED,
        RunState.COMPLETED,
        RunState.FAILED,
        RunState.CANCELLED,
    },
    RunState.PAUSED: {RunState.RUNNING, RunState.FAILED, RunState.CANCELLED},
    RunState.COMPLETED: set(),
    RunState.FAILED: set(),
    RunState.CANCELLED: set(),
}


def can_transition(current: RunState, target: RunState) -> bool:
    """Return True if the run may move from ``current`` to ``target``."""
    return target in _RUN_TRANSITIONS[current]


class TriggerOrigin(BaseModel):
    """Attribution metadata for a trigger-originated run."""

    model_config = ConfigDict(extra="forbid")

    source: str = Field(min_length=1)
    event_id: str = Field(min_length=1)
    timestamp: AwareDatetime


class PipelineOrigin(BaseModel):
    """Attribution metadata for a pipeline-node child run (M29-02)."""

    model_config = ConfigDict(extra="forbid")

    pipeline_run_id: str = Field(min_length=1)
    pipeline_id: str = Field(min_length=1)
    node_id: str = Field(min_length=1)
    parent_run_id: str | None = None
    attempt: int = Field(default=1, ge=1)


class Run(BaseModel):
    """A single execution of a workload through the control plane."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1)
    workload_id: str = Field(min_length=1)
    caller: str = Field(min_length=1)
    state: RunState
    model_identity: str | None = None
    created_at: AwareDatetime
    updated_at: AwareDatetime
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None
    trigger_origin: TriggerOrigin | None = None
    pipeline_origin: PipelineOrigin | None = None
    manifest_version: int | None = None
    context: AdmissionContext | None = None
    sandbox: bool = False
    task: dict[str, JsonValue] = Field(default_factory=dict)
    result: JsonValue | None = None
    failure_reason: str | None = None
    cost_usd: float = Field(default=0.0, ge=0.0)
    sandbox_id: str | None = None
    trace_id: str | None = None
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    team_id: str | None = None
    attribution_key: str | None = None
