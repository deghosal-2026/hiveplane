"""Pipeline models (M25-03, D21/D24).

A pipeline is a versioned DAG of workload and gate nodes. Edges carry an
optional output-to-input handoff projection; nodes may declare a per-step gate
and the pipeline carries its own budget. Validation rejects malformed DAGs
(duplicate node ids, unknown edge endpoints, cycles, gate/handoff mismatches)
with actionable errors.
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, model_validator

from hiveplane.tenancy.context import DEFAULT_TENANT_ID


class PipelineNodeKind(StrEnum):
    """The kind of work a pipeline node performs."""

    WORKLOAD = "workload"
    GATE = "gate"


class GateKind(StrEnum):
    """A per-step gate that must pass before dependents run."""

    CERT = "cert"
    PRODUCTION = "production"
    APPROVAL = "approval"


class PipelineState(StrEnum):
    """Lifecycle state of a pipeline run."""

    PENDING = "pending"
    RUNNING = "running"
    PAUSED = "paused"
    COMPLETED = "completed"
    FAILED = "failed"
    CANCELLED = "cancelled"


class StepGate(BaseModel):
    """A gate attached to a pipeline node."""

    model_config = ConfigDict(extra="forbid")

    kind: GateKind
    required: bool = True
    reason: str | None = Field(default=None, max_length=2000)


class PipelineNode(BaseModel):
    """One workload or gate node in a pipeline DAG."""

    model_config = ConfigDict(extra="forbid")

    node_id: str = Field(min_length=1, max_length=128)
    kind: PipelineNodeKind
    ref: str | None = Field(default=None, max_length=253)
    gate: StepGate | None = None
    depends_on: list[str] = Field(default_factory=list)
    retries: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def _kind_requirements(self) -> Self:
        if self.kind is PipelineNodeKind.WORKLOAD and not self.ref:
            raise ValueError(f"workload node {self.node_id!r} requires 'ref'")
        if self.kind is PipelineNodeKind.GATE and self.gate is None:
            raise ValueError(f"gate node {self.node_id!r} requires a 'gate'")
        if self.kind is PipelineNodeKind.WORKLOAD and self.gate is not None:
            raise ValueError(f"workload node {self.node_id!r} must not declare a 'gate'")
        return self


class HandoffMapping(BaseModel):
    """Projects an upstream node's output onto a downstream node's input."""

    model_config = ConfigDict(extra="forbid")

    from_node: str = Field(min_length=1, max_length=128)
    to_node: str = Field(min_length=1, max_length=128)
    projection: dict[str, str] = Field(default_factory=dict)


class PipelineEdge(BaseModel):
    """A directed dependency between two pipeline nodes."""

    model_config = ConfigDict(extra="forbid")

    from_node: str = Field(min_length=1, max_length=128)
    to_node: str = Field(min_length=1, max_length=128)
    handoff: HandoffMapping | None = None


class PipelineBudget(BaseModel):
    """Spend limits for a pipeline."""

    model_config = ConfigDict(extra="forbid")

    per_run_usd: float = Field(default=0.0, ge=0.0)
    total_usd: float = Field(default=0.0, ge=0.0)


class Pipeline(BaseModel):
    """A versioned pipeline DAG."""

    model_config = ConfigDict(extra="forbid")

    pipeline_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=253)
    version: int = Field(ge=1)
    nodes: list[PipelineNode] = Field(min_length=1)
    edges: list[PipelineEdge] = Field(default_factory=list)
    budget: PipelineBudget = Field(default_factory=PipelineBudget)
    created_at: AwareDatetime

    @model_validator(mode="after")
    def _valid_dag(self) -> Self:
        ids = [node.node_id for node in self.nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("pipeline node ids must be unique")
        known = set(ids)
        for node in self.nodes:
            unknown = [dep for dep in node.depends_on if dep not in known]
            if unknown:
                raise ValueError(
                    f"node {node.node_id!r} depends on unknown node(s): {sorted(unknown)}"
                )
            if node.node_id in node.depends_on:
                raise ValueError(f"node {node.node_id!r} cannot depend on itself")
        for edge in self.edges:
            if edge.from_node not in known or edge.to_node not in known:
                raise ValueError(
                    f"edge {edge.from_node!r}->{edge.to_node!r} references an unknown node"
                )
            if edge.from_node == edge.to_node:
                raise ValueError(f"edge {edge.from_node!r} cannot be a self-loop")
            if edge.handoff is not None and (
                edge.handoff.from_node != edge.from_node
                or edge.handoff.to_node != edge.to_node
            ):
                raise ValueError(
                    f"handoff endpoints must match edge {edge.from_node!r}->{edge.to_node!r}"
                )
        if _has_cycle(ids, [(edge.from_node, edge.to_node) for edge in self.edges]):
            raise ValueError("pipeline edges must form a DAG (cycle detected)")
        return self


def _has_cycle(node_ids: list[str], edges: list[tuple[str, str]]) -> bool:
    """Return True when the directed graph contains a cycle."""
    adjacency: dict[str, list[str]] = {node_id: [] for node_id in node_ids}
    for source, target in edges:
        adjacency[source].append(target)
    visiting: set[str] = set()
    visited: set[str] = set()

    def visit(node_id: str) -> bool:
        if node_id in visiting:
            return True
        if node_id in visited:
            return False
        visiting.add(node_id)
        for neighbour in adjacency[node_id]:
            if visit(neighbour):
                return True
        visiting.discard(node_id)
        visited.add(node_id)
        return False

    return any(visit(node_id) for node_id in node_ids)


class PipelineRun(BaseModel):
    """Links a pipeline run to one of its nodes' child runs."""

    model_config = ConfigDict(extra="forbid")

    pipeline_run_id: str = Field(min_length=1, max_length=64)
    pipeline_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    version: int = Field(ge=1)
    node_id: str = Field(min_length=1, max_length=128)
    parent_run_id: str | None = Field(default=None, max_length=64)
    child_run_id: str | None = Field(default=None, max_length=64)
    state: PipelineState
    cost_usd: float = Field(default=0.0, ge=0.0)
    started_at: AwareDatetime
    finished_at: AwareDatetime | None = None
