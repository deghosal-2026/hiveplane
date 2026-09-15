"""Run lifecycle state and the Run aggregate (D2)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class RunState(StrEnum):
    """Observable states of a run."""

    QUEUED = "queued"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


_RUN_TRANSITIONS: dict[RunState, set[RunState]] = {
    RunState.QUEUED: {RunState.RUNNING},
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
