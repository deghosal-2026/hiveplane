"""Pipeline execution models: parent runs, node runs, and node results (M29-02)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

from hiveplane.fleet.pipelines import PipelineState
from hiveplane.pipelines.handoff import PipelineContext
from hiveplane.tenancy.context import DEFAULT_TENANT_ID


class NodeStatus(StrEnum):
    """Lifecycle state of a pipeline node execution."""

    PENDING = "pending"
    RUNNING = "running"
    WAITING_APPROVAL = "waiting_approval"
    COMPLETED = "completed"
    FAILED = "failed"
    SKIPPED = "skipped"


class NodeResult(BaseModel):
    """The terminal result of one child run, as seen by the pipeline engine."""

    model_config = ConfigDict(extra="forbid")

    status: NodeStatus
    output: JsonValue = None
    cost_usd: float = Field(default=0.0, ge=0.0)
    error: str | None = Field(default=None, max_length=2000)
    artifacts: list[str] = Field(default_factory=list)


class FanOutInstance(BaseModel):
    """One mapped child of a fan-out node."""

    model_config = ConfigDict(extra="forbid")

    index: int = Field(ge=0)
    item: JsonValue = None
    child_run_id: str | None = None
    status: NodeStatus = NodeStatus.PENDING
    output: JsonValue = None
    cost_usd: float = Field(default=0.0, ge=0.0)
    error: str | None = Field(default=None, max_length=2000)


class PipelineNodeRun(BaseModel):
    """One node's execution within a pipeline run."""

    model_config = ConfigDict(extra="forbid")

    pipeline_run_id: str = Field(min_length=1, max_length=64)
    pipeline_id: str = Field(min_length=1, max_length=64)
    node_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    attempt: int = Field(default=1, ge=1)
    status: NodeStatus = NodeStatus.PENDING
    child_run_id: str | None = Field(default=None, max_length=64)
    output: JsonValue = None
    cost_usd: float = Field(default=0.0, ge=0.0)
    error: str | None = Field(default=None, max_length=2000)
    approval_id: str | None = Field(default=None, max_length=64)
    artifacts: list[str] = Field(default_factory=list)
    instances: list[FanOutInstance] = Field(default_factory=list)
    started_at: AwareDatetime | None = None
    finished_at: AwareDatetime | None = None


class PipelineRunHeader(BaseModel):
    """The parent record of one pipeline execution."""

    model_config = ConfigDict(extra="forbid")

    pipeline_run_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    pipeline_id: str = Field(min_length=1, max_length=64)
    version: int = Field(default=1, ge=1)
    state: PipelineState = PipelineState.PENDING
    budget_usd: float = Field(default=0.0, ge=0.0)
    spent_usd: float = Field(default=0.0, ge=0.0)
    parent_run_id: str | None = Field(default=None, max_length=64)
    context: PipelineContext = Field(default_factory=PipelineContext)
    error: str | None = Field(default=None, max_length=2000)
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None


class PipelineTimeline(BaseModel):
    """A derived, read-only view of a pipeline run and its node timeline."""

    model_config = ConfigDict(extra="forbid")

    pipeline_run_id: str
    pipeline_id: str
    state: PipelineState
    budget_usd: float = 0.0
    spent_usd: float = 0.0
    error: str | None = None
    nodes: list[PipelineNodeRun] = Field(default_factory=list)
