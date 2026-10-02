"""Typed records for time-travel replay, fork, diff, and A/B replay (M60)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

from hiveplane.core.run import RunState
from hiveplane.core.usage import UsageReport
from hiveplane.tenancy.context import DEFAULT_TENANT_ID


class ReplayMode(StrEnum):
    """Which replay operation produced a record."""

    REPLAY = "replay"
    FORK = "fork"
    AB = "ab"


class ReplayFrame(BaseModel):
    """One ordered step in a run's reconstructed execution history."""

    model_config = ConfigDict(extra="forbid")

    sequence: int = Field(ge=0)
    timestamp: AwareDatetime
    event_type: str = Field(min_length=1)
    actor: str = Field(min_length=1)
    from_state: RunState | None = None
    to_state: RunState | None = None
    detail: str | None = None
    usage: UsageReport | None = None


class ReplayFrameSet(BaseModel):
    """A deterministic, side-effect-free reconstruction of a run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    frames: list[ReplayFrame] = Field(default_factory=list)
    frame_count: int = Field(ge=0)
    digest: str = Field(min_length=1)
    side_effects: bool = False


class FieldDelta(BaseModel):
    """A single changed field between two runs."""

    model_config = ConfigDict(extra="forbid")

    field: str = Field(min_length=1)
    before: JsonValue | None = None
    after: JsonValue | None = None


class RunDiff(BaseModel):
    """A run-to-run comparison of state, calls, cost, and outcome (M60-02)."""

    model_config = ConfigDict(extra="forbid")

    source_run_id: str = Field(min_length=1)
    target_run_id: str = Field(min_length=1)
    identical: bool
    state_before: RunState
    state_after: RunState
    result_changed: bool
    failure_changed: bool
    cost_delta_usd: float = 0.0
    latency_delta_ms: int = 0
    tool_calls_added: list[dict[str, JsonValue]] = Field(default_factory=list)
    tool_calls_removed: list[dict[str, JsonValue]] = Field(default_factory=list)
    model_calls_added: list[dict[str, JsonValue]] = Field(default_factory=list)
    model_calls_removed: list[dict[str, JsonValue]] = Field(default_factory=list)
    field_deltas: list[FieldDelta] = Field(default_factory=list)


class ReplayRecord(BaseModel):
    """An audited replay/fork/A-B operation over a source run."""

    model_config = ConfigDict(extra="forbid")

    replay_id: str = Field(min_length=1)
    source_run_id: str = Field(min_length=1)
    mode: ReplayMode
    actor: str = Field(min_length=1)
    fork_point: int | None = Field(default=None, ge=0)
    side_effects: bool = False
    run_ids: list[str] = Field(default_factory=list)
    detail: str | None = None
    created_at: AwareDatetime
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)


class ForkResult(BaseModel):
    """The outcome of forking a run: the new run and its replay record."""

    model_config = ConfigDict(extra="forbid")

    replay_id: str = Field(min_length=1)
    source_run_id: str = Field(min_length=1)
    forked_run_id: str = Field(min_length=1)
    task: dict[str, JsonValue] = Field(default_factory=dict)
    fork_point: int | None = None
    side_effects: bool = False


class ABReplayResult(BaseModel):
    """A side-by-side A/B replay of two workloads on identical input (M60-04)."""

    model_config = ConfigDict(extra="forbid")

    replay_id: str = Field(min_length=1)
    source_run_id: str = Field(min_length=1)
    run_a_id: str = Field(min_length=1)
    run_b_id: str = Field(min_length=1)
    workload_a: str = Field(min_length=1)
    workload_b: str = Field(min_length=1)
    side_effects: bool = False
    diff: RunDiff
