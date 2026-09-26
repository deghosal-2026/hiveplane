"""The declarative pipeline DSL: a validated DAG of agents and control steps (M29-01).

Nodes are workloads or control steps (fan-out, fan-in, gate, transform); edges
express data and ordering dependencies. Validation rejects duplicate ids, dangling
edges, self-loops, unknown references, non-upstream data references, and cycles
(Kahn's algorithm, naming the offending nodes) before any run starts.
"""

from __future__ import annotations

import re
from enum import StrEnum

from pydantic import (
    AliasChoices,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from hiveplane.tenancy.context import DEFAULT_TENANT_ID

_REF = re.compile(r"\$\{([^}]+)\}")


class NodeKind(StrEnum):
    """The kind of work a pipeline node performs."""

    WORKLOAD = "workload"
    FAN_OUT = "fan_out"
    FAN_IN = "fan_in"
    GATE = "gate"
    TRANSFORM = "transform"


class OnFailure(StrEnum):
    """What a node failure does to the rest of the pipeline."""

    FAIL_FAST = "fail_fast"
    CONTINUE = "continue"


class ApprovalTiming(StrEnum):
    """When a node's approval gate interrupts."""

    BEFORE = "before"
    AFTER = "after"


class RetryMode(StrEnum):
    """How a retry starts."""

    RESTART = "restart"
    CHECKPOINT = "checkpoint"


class Reducer(StrEnum):
    """Built-in fan-in reducers."""

    JSON_MERGE = "json_merge"
    CONCAT = "concat"
    SUM = "sum"
    FIRST_SUCCESS = "first_success"


class OnExceed(StrEnum):
    """What happens when the pipeline budget is exhausted."""

    PAUSE = "pause"
    FAIL = "fail"


class RetrySpec(BaseModel):
    """Retry policy for a node."""

    model_config = ConfigDict(extra="forbid")

    max_attempts: int = Field(default=1, ge=1, le=10)
    backoff_s: float = Field(default=0.0, ge=0.0)
    mode: RetryMode = RetryMode.RESTART


class NodeBudgetSpec(BaseModel):
    """A per-step spend cap."""

    model_config = ConfigDict(extra="forbid")

    usd: float = Field(gt=0.0)


class PipelineNodeSpec(BaseModel):
    """One node in a pipeline DAG."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    id: str = Field(min_length=1, max_length=128)
    kind: NodeKind
    workload: str | None = Field(default=None, max_length=253)
    inputs: dict[str, str] = Field(default_factory=dict)
    on_failure: OnFailure = OnFailure.FAIL_FAST
    retry: RetrySpec = Field(default_factory=RetrySpec)
    requires_approval: ApprovalTiming | None = None
    budget: NodeBudgetSpec | None = None
    depends_on: list[str] = Field(default_factory=list)
    # fan_out
    over: str | None = Field(default=None, max_length=512)
    as_name: str | None = Field(default=None, validation_alias=AliasChoices("as", "as_name"))
    node: str | None = Field(default=None, max_length=128)
    # fan_in
    from_nodes: list[str] = Field(
        default_factory=list, validation_alias=AliasChoices("from", "from_nodes")
    )
    reducer: Reducer | None = None
    # transform
    transform: dict[str, str] | None = None

    @model_validator(mode="after")
    def _kind_requirements(self) -> PipelineNodeSpec:
        if self.kind is NodeKind.WORKLOAD and not self.workload:
            raise ValueError(f"workload node {self.id!r} requires 'workload'")
        if self.kind is NodeKind.FAN_OUT and not (self.over and self.node and self.as_name):
            raise ValueError(
                f"fan_out node {self.id!r} requires 'over', 'node', and 'as'"
            )
        if self.kind is NodeKind.FAN_IN and not (self.from_nodes and self.reducer):
            raise ValueError(f"fan_in node {self.id!r} requires 'from' and 'reducer'")
        if self.kind is NodeKind.TRANSFORM and not self.transform:
            raise ValueError(f"transform node {self.id!r} requires 'transform'")
        return self


class PipelineEdgeSpec(BaseModel):
    """A directed dependency between two nodes with an optional handoff mapping."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    from_node: str = Field(validation_alias=AliasChoices("from", "from_node"))
    to_node: str = Field(validation_alias=AliasChoices("to", "to_node"))
    handoff: dict[str, str] = Field(default_factory=dict)


class PipelineBudgetSpec(BaseModel):
    """The cumulative spend cap for a pipeline run."""

    model_config = ConfigDict(extra="forbid")

    usd: float = Field(default=0.0, ge=0.0)
    on_exceed: OnExceed = OnExceed.PAUSE


class PipelineSpec(BaseModel):
    """A validated pipeline DAG."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    name: str = Field(min_length=1, max_length=253)
    version: int = Field(default=1, ge=1)
    nodes: list[PipelineNodeSpec] = Field(min_length=1)
    edges: list[PipelineEdgeSpec] = Field(default_factory=list)
    budget: PipelineBudgetSpec = Field(default_factory=PipelineBudgetSpec)
    created_at: AwareDatetime | None = None

    @field_validator("nodes")
    @classmethod
    def _node_ids_unique(
        cls, nodes: list[PipelineNodeSpec]
    ) -> list[PipelineNodeSpec]:
        ids = [node.id for node in nodes]
        if len(ids) != len(set(ids)):
            raise ValueError("pipeline node ids must be unique")
        return nodes

    @model_validator(mode="after")
    def _valid_dag(self) -> PipelineSpec:
        ids = [node.id for node in self.nodes]
        known = set(ids)
        for node in self.nodes:
            for dep in node.depends_on:
                if dep not in known:
                    raise ValueError(f"node {node.id!r} depends on unknown node {dep!r}")
                if dep == node.id:
                    raise ValueError(f"node {node.id!r} cannot depend on itself")
            if node.kind is NodeKind.FAN_OUT and node.node not in known:
                raise ValueError(
                    f"fan_out node {node.id!r} references unknown template node {node.node!r}"
                )
            for source in node.from_nodes:
                if source not in known:
                    raise ValueError(
                        f"fan_in node {node.id!r} references unknown node {source!r}"
                    )
        for edge in self.edges:
            if edge.from_node not in known or edge.to_node not in known:
                raise ValueError(
                    f"edge {edge.from_node!r}->{edge.to_node!r} references an unknown node"
                )
            if edge.from_node == edge.to_node:
                raise ValueError(f"edge {edge.from_node!r} cannot be a self-loop")
        self._validate_references(known)
        self._reject_cycles(ids)
        return self

    def node(self, node_id: str) -> PipelineNodeSpec | None:
        """Return the node with ``node_id``, or None."""
        for node in self.nodes:
            if node.id == node_id:
                return node
        return None

    def dependencies(self, node_id: str) -> list[str]:
        """Return a node's direct dependencies (edges plus declared depends_on)."""
        deps = {
            edge.from_node for edge in self.edges if edge.to_node == node_id
        }
        node = self.node(node_id)
        if node is not None:
            deps.update(node.depends_on)
        return sorted(deps)

    def _ancestors(self, node_id: str) -> set[str]:
        ancestors: set[str] = set()
        frontier = list(self.dependencies(node_id))
        while frontier:
            current = frontier.pop()
            if current in ancestors:
                continue
            ancestors.add(current)
            frontier.extend(self.dependencies(current))
        return ancestors

    def _validate_references(self, known: set[str]) -> None:
        for node in self.nodes:
            for value in [*node.inputs.values(), node.over or "", *(node.transform or {}).values()]:
                for reference in _references(value):
                    if reference not in known:
                        raise ValueError(
                            f"node {node.id!r} references unknown node {reference!r}"
                        )
                    if reference not in self._ancestors(node.id):
                        raise ValueError(
                            f"node {node.id!r} references {reference!r}, which is not upstream"
                        )

    def _reject_cycles(self, ids: list[str]) -> None:
        indegree = dict.fromkeys(ids, 0)
        adjacency: dict[str, list[str]] = {node_id: [] for node_id in ids}
        for edge in self.edges:
            adjacency[edge.from_node].append(edge.to_node)
            indegree[edge.to_node] += 1
        for node in self.nodes:
            for dep in node.depends_on:
                adjacency[dep].append(node.id)
                indegree[node.id] += 1
        ready = [node_id for node_id in ids if indegree[node_id] == 0]
        visited = 0
        while ready:
            current = ready.pop()
            visited += 1
            for neighbour in adjacency[current]:
                indegree[neighbour] -= 1
                if indegree[neighbour] == 0:
                    ready.append(neighbour)
        if visited != len(ids):
            cyclic = sorted(node_id for node_id in ids if indegree[node_id] > 0)
            raise ValueError(f"pipeline contains a cycle among nodes: {cyclic}")


def _references(template: str) -> set[str]:
    """Return the node ids referenced by ``${node.output...}`` style templates."""
    refs: set[str] = set()
    for match in _REF.finditer(template):
        segments = match.group(1).strip().split(".")
        if len(segments) >= 2 and segments[1] in ("output", "input", "cost", "status"):
            refs.add(segments[0])
    return refs
