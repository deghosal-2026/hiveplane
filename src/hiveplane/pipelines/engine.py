"""The pipeline execution engine (M29-02..M29-08).

A submission creates a parent pipeline-run record and the engine walks the DAG:
each ready node submits a child run (with ``pipeline_origin`` attribution),
handoffs are schema-validated at both boundaries, fan-out maps over a list and
fan-in reduces, approval gates pause and resume, cumulative spend is capped by
the pipeline budget, and failures honor ``fail_fast``/``continue`` with retries.
Parent state is derived from node records; the child runs stay the source of
truth.
"""

from __future__ import annotations

import uuid
from collections.abc import Callable
from datetime import UTC, datetime
from typing import Protocol

from pydantic import JsonValue

from hiveplane.core.run import AdmissionContext, PipelineOrigin
from hiveplane.core.spec import IOSpec
from hiveplane.fleet.pipelines import PipelineState
from hiveplane.pipelines.handoff import (
    HandoffError,
    PipelineContext,
    map_inputs,
    render_ref,
    validate_boundary,
)
from hiveplane.pipelines.models import (
    FanOutInstance,
    NodeResult,
    NodeStatus,
    PipelineNodeRun,
    PipelineRunHeader,
    PipelineTimeline,
)
from hiveplane.pipelines.spec import (
    NodeKind,
    OnExceed,
    OnFailure,
    PipelineNodeSpec,
    PipelineSpec,
    Reducer,
)
from hiveplane.pipelines.store import PipelineStore
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext

_TERMINAL = (PipelineState.COMPLETED, PipelineState.FAILED, PipelineState.CANCELLED)
_DONE = (NodeStatus.COMPLETED, NodeStatus.FAILED, NodeStatus.SKIPPED)
_MAX_ITERATIONS = 200


class PipelineRunNotFoundError(Exception):
    """Raised when a pipeline run does not exist."""

    def __init__(self, pipeline_run_id: str) -> None:
        super().__init__(f"pipeline run {pipeline_run_id!r} not found")
        self.pipeline_run_id = pipeline_run_id


class PipelineSpecNotFoundError(Exception):
    """Raised when a pipeline spec does not exist."""

    def __init__(self, pipeline_id: str) -> None:
        super().__init__(f"pipeline {pipeline_id!r} not found")
        self.pipeline_id = pipeline_id


class NodeExecutor(Protocol):
    """Submits and polls the child runs of pipeline nodes."""

    def submit(
        self,
        *,
        workload: str,
        inputs: dict[str, JsonValue],
        context: AdmissionContext,
        origin: PipelineOrigin,
        ctx: TenantContext,
    ) -> str: ...

    def result(self, child_run_id: str, *, ctx: TenantContext) -> NodeResult | None: ...

    def cancel(self, child_run_id: str, *, ctx: TenantContext) -> None: ...


class ApprovalGate(Protocol):
    """Requests and polls per-step approvals."""

    def request(self, *, run_id: str, workload: str, rule: str, reason: str) -> str: ...

    def status(self, approval_id: str) -> str: ...


class PipelineEngine:
    """Executes a pipeline DAG over child runs."""

    def __init__(
        self,
        store: PipelineStore,
        executor: NodeExecutor,
        *,
        approvals: ApprovalGate | None = None,
        schema_lookup: Callable[[str], IOSpec | None] | None = None,
        budget_lookup: Callable[[str], float | None] | None = None,
        clock: Callable[[], datetime] | None = None,
        id_factory: Callable[[], str] | None = None,
    ) -> None:
        self._store = store
        self._executor = executor
        self._approvals = approvals
        self._schema_lookup = schema_lookup or (lambda workload: None)
        self._budget_lookup = budget_lookup or (lambda workload: None)
        self._clock = clock or (lambda: datetime.now(UTC))
        self._id_factory = id_factory or (lambda: f"prun-{uuid.uuid4().hex[:12]}")

    # ------------------------------------------------------------------ #
    # Submission / driving
    # ------------------------------------------------------------------ #
    def submit(
        self,
        spec: PipelineSpec,
        *,
        inputs: dict[str, JsonValue] | None = None,
        context: AdmissionContext = AdmissionContext.STAGING,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> PipelineRunHeader:
        """Register the spec, create a parent run, and advance it."""
        self._store.save_spec(spec, ctx=ctx)
        pipeline_context = PipelineContext()
        if inputs:
            pipeline_context.variables.update(inputs)
        header = PipelineRunHeader(
            pipeline_run_id=self._id_factory(),
            tenant_id=ctx.tenant_id,
            pipeline_id=spec.id,
            version=spec.version,
            state=PipelineState.RUNNING,
            budget_usd=spec.budget.usd,
            context=pipeline_context,
            started_at=self._clock(),
        )
        self._store.save_run(header, ctx=ctx)
        return self.advance(header.pipeline_run_id, ctx=ctx)

    def advance(
        self, pipeline_run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> PipelineRunHeader:
        """Drive the pipeline as far as the child runs allow."""
        header = self._store.get_run(pipeline_run_id, ctx=ctx)
        if header is None:
            raise PipelineRunNotFoundError(pipeline_run_id)
        if header.state in _TERMINAL:
            return header
        spec = self._store.get_spec(header.pipeline_id, ctx=ctx)
        if spec is None:
            raise PipelineSpecNotFoundError(header.pipeline_id)
        managed = _fan_out_templates(spec)
        context = header.context

        for _ in range(_MAX_ITERATIONS):
            changed = False
            nodes = self._latest_nodes(pipeline_run_id, ctx)
            for node in list(nodes.values()):
                if node.status is NodeStatus.RUNNING:
                    reconciled = self._reconcile(node, spec, context, header, ctx)
                    if reconciled is not None:
                        nodes[node.node_id] = reconciled
                        self._store.save_node_run(reconciled, ctx=ctx)
                        changed = True
            header.spent_usd = _spent(nodes)
            exceed = self._budget_exceeded(header)
            if exceed is not None:
                header.state, header.error = exceed
                self._store.save_run(header, ctx=ctx)
                return header
            for spec_node in _topological_order(spec):
                if spec_node.id in managed:
                    continue
                current = nodes.get(spec_node.id)
                if current is not None and current.status in _DONE:
                    continue
                if current is not None and current.status is NodeStatus.RUNNING:
                    continue
                dependency_states = [
                    nodes[dep].status if dep in nodes else NodeStatus.PENDING
                    for dep in spec.dependencies(spec_node.id)
                ]
                if any(
                    state in (NodeStatus.FAILED, NodeStatus.SKIPPED)
                    for state in dependency_states
                ):
                    skipped = self._node_run(header, spec_node).model_copy(
                        update={"status": NodeStatus.SKIPPED, "error": "dependency failed"}
                    )
                    nodes[spec_node.id] = skipped
                    self._store.save_node_run(skipped, ctx=ctx)
                    changed = True
                    continue
                if any(state is not NodeStatus.COMPLETED for state in dependency_states):
                    continue
                projected = self._projected_cost(spec_node)
                if header.budget_usd > 0 and header.spent_usd + projected > header.budget_usd:
                    header.state = (
                        PipelineState.PAUSED
                        if spec.budget.on_exceed is OnExceed.PAUSE
                        else PipelineState.FAILED
                    )
                    header.error = "pipeline budget exhausted"
                    self._store.save_run(header, ctx=ctx)
                    return header
                updated = self._start(spec_node, spec, context, header, nodes, ctx, current)
                if updated is not None:
                    nodes[spec_node.id] = updated
                    self._store.save_node_run(updated, ctx=ctx)
                    header.spent_usd = _spent(nodes)
                    changed = True
            header.spent_usd = _spent(nodes)
            self._skip_after_fail_fast(spec, nodes, header, ctx)
            new_state = self._derive_state(spec, nodes)
            if new_state is not header.state:
                header.state = new_state
                changed = True
            if header.state in _TERMINAL:
                header.finished_at = self._clock()
            self._store.save_run(header, ctx=ctx)
            if not changed or header.state in _TERMINAL:
                break
        return header

    def retry_node(
        self, pipeline_run_id: str, node_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> PipelineRunHeader:
        """Start a new attempt of a failed node and advance the pipeline."""
        header = self._store.get_run(pipeline_run_id, ctx=ctx)
        if header is None:
            raise PipelineRunNotFoundError(pipeline_run_id)
        spec = self._store.get_spec(header.pipeline_id, ctx=ctx)
        if spec is None:
            raise PipelineSpecNotFoundError(header.pipeline_id)
        nodes = self._latest_nodes(pipeline_run_id, ctx)
        current = nodes.get(node_id)
        if current is None:
            raise PipelineRunNotFoundError(pipeline_run_id)
        attempt = PipelineNodeRun(
            pipeline_run_id=pipeline_run_id,
            pipeline_id=header.pipeline_id,
            node_id=node_id,
            attempt=current.attempt + 1,
            status=NodeStatus.PENDING,
            started_at=self._clock(),
        )
        self._store.save_node_run(attempt, ctx=ctx)
        for other in nodes.values():
            if other.node_id == node_id or other.status is not NodeStatus.SKIPPED:
                continue
            reset = other.model_copy(
                update={
                    "attempt": other.attempt + 1,
                    "status": NodeStatus.PENDING,
                    "child_run_id": None,
                    "error": None,
                }
            )
            self._store.save_node_run(reset, ctx=ctx)
        header.state = PipelineState.RUNNING
        header.error = None
        header.finished_at = None
        self._store.save_run(header, ctx=ctx)
        return self.advance(pipeline_run_id, ctx=ctx)

    def timeline(
        self, pipeline_run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> PipelineTimeline:
        """Return the parent state and per-node timeline."""
        header = self._store.get_run(pipeline_run_id, ctx=ctx)
        if header is None:
            raise PipelineRunNotFoundError(pipeline_run_id)
        spec = self._store.get_spec(header.pipeline_id, ctx=ctx)
        latest = self._latest_nodes(pipeline_run_id, ctx)
        if spec is not None:
            ordered = [
                latest[node.id] for node in _topological_order(spec) if node.id in latest
            ]
        else:
            ordered = list(latest.values())
        return PipelineTimeline(
            pipeline_run_id=header.pipeline_run_id,
            pipeline_id=header.pipeline_id,
            state=header.state,
            budget_usd=header.budget_usd,
            spent_usd=header.spent_usd,
            error=header.error,
            nodes=ordered,
        )

    # ------------------------------------------------------------------ #
    # Node execution
    # ------------------------------------------------------------------ #
    def _start(
        self,
        spec_node: PipelineNodeSpec,
        spec: PipelineSpec,
        context: PipelineContext,
        header: PipelineRunHeader,
        nodes: dict[str, PipelineNodeRun],
        ctx: TenantContext,
        current: PipelineNodeRun | None = None,
    ) -> PipelineNodeRun | None:
        node = current or self._node_run(header, spec_node)
        if spec_node.requires_approval is not None or spec_node.kind is NodeKind.GATE:
            return self._gate(spec_node, node, context, header, ctx)
        if spec_node.kind is NodeKind.TRANSFORM:
            transform = spec_node.transform or {}
            output = {key: render_ref(value, context) for key, value in transform.items()}
            return node.model_copy(
                update={
                    "status": NodeStatus.COMPLETED,
                    "output": output,
                    "finished_at": self._clock(),
                }
            )
        if spec_node.kind is NodeKind.FAN_OUT:
            return self._fan_out(spec_node, spec, context, node, header, ctx)
        if spec_node.kind is NodeKind.FAN_IN:
            return self._fan_in(spec_node, context, node)
        return self._workload(spec_node, context, node, header, ctx)

    def _gate(
        self,
        spec_node: PipelineNodeSpec,
        node: PipelineNodeRun,
        context: PipelineContext,
        header: PipelineRunHeader,
        ctx: TenantContext,
    ) -> PipelineNodeRun:
        if self._approvals is None:
            return node.model_copy(
                update={
                    "status": NodeStatus.FAILED,
                    "error": "approval gate not configured",
                    "finished_at": self._clock(),
                }
            )
        if node.approval_id is None:
            approval_id = self._approvals.request(
                run_id=node.pipeline_run_id,
                workload=spec_node.workload or spec_node.id,
                rule="pipeline.gate",
                reason=f"approval required before {spec_node.id!r}",
            )
            return node.model_copy(
                update={
                    "status": NodeStatus.WAITING_APPROVAL,
                    "approval_id": approval_id,
                    "started_at": node.started_at or self._clock(),
                }
            )
        status = self._approvals.status(node.approval_id)
        if status == "pending":
            return node
        if status == "denied":
            return node.model_copy(
                update={
                    "status": NodeStatus.FAILED,
                    "error": "approval denied",
                    "finished_at": self._clock(),
                }
            )
        if spec_node.kind is NodeKind.GATE:
            return node.model_copy(
                update={"status": NodeStatus.COMPLETED, "finished_at": self._clock()}
            )
        # Approved workload node: execute now (gate cleared).
        cleared = node.model_copy(update={"approval_id": None})
        return self._workload(spec_node, context, cleared, header, ctx)

    def _workload(
        self,
        spec_node: PipelineNodeSpec,
        context: PipelineContext,
        node: PipelineNodeRun,
        header: PipelineRunHeader,
        ctx: TenantContext,
    ) -> PipelineNodeRun:
        assert spec_node.workload is not None
        try:
            mapped = map_inputs(spec_node.inputs, context)
            io = self._schema_lookup(spec_node.workload)
            validate_boundary(
                mapped, io.input_schema if io else None, node=spec_node.id, boundary="input"
            )
        except HandoffError as exc:
            return self._failed(spec_node, node, str(exc))
        origin = PipelineOrigin(
            pipeline_run_id=node.pipeline_run_id,
            pipeline_id=node.pipeline_id,
            node_id=spec_node.id,
            parent_run_id=header.parent_run_id,
            attempt=node.attempt,
        )
        child_run_id = self._executor.submit(
            workload=spec_node.workload,
            inputs=mapped,
            context=AdmissionContext.STAGING,
            origin=origin,
            ctx=ctx,
        )
        running = node.model_copy(
            update={
                "status": NodeStatus.RUNNING,
                "child_run_id": child_run_id,
                "started_at": node.started_at or self._clock(),
            }
        )
        result = self._executor.result(child_run_id, ctx=ctx)
        if result is None:
            return running
        return self._finish(spec_node, running, result, context, ctx)

    def _reconcile(
        self,
        node: PipelineNodeRun,
        spec: PipelineSpec,
        context: PipelineContext,
        header: PipelineRunHeader,
        ctx: TenantContext,
    ) -> PipelineNodeRun | None:
        spec_node = spec.node(node.node_id)
        if spec_node is None:
            return None
        if spec_node.kind is NodeKind.FAN_OUT:
            return self._reconcile_fan_out(spec_node, node, context, ctx)
        if node.child_run_id is None:
            return None
        result = self._executor.result(node.child_run_id, ctx=ctx)
        if result is None:
            return None
        return self._finish(spec_node, node, result, context, ctx)

    def _finish(
        self,
        spec_node: PipelineNodeSpec,
        node: PipelineNodeRun,
        result: NodeResult,
        context: PipelineContext,
        ctx: TenantContext,
    ) -> PipelineNodeRun:
        if result.status is NodeStatus.COMPLETED:
            if spec_node.budget is not None and result.cost_usd > spec_node.budget.usd:
                return self._failed(
                    spec_node, node, "per-step budget exceeded", result.cost_usd
                )
            io = self._schema_lookup(spec_node.workload or "")
            try:
                validate_boundary(
                    result.output,
                    io.output_schema if io else None,
                    node=spec_node.id,
                    boundary="output",
                )
            except HandoffError as exc:
                return self._failed(spec_node, node, str(exc))
            finished = node.model_copy(
                update={
                    "status": NodeStatus.COMPLETED,
                    "output": result.output,
                    "cost_usd": result.cost_usd,
                    "artifacts": result.artifacts,
                    "finished_at": self._clock(),
                }
            )
            self._publish(context, finished)
            return finished
        return self._failed(spec_node, node, result.error or "node failed", result.cost_usd)

    def _failed(
        self,
        spec_node: PipelineNodeSpec,
        node: PipelineNodeRun,
        error: str,
        cost_usd: float = 0.0,
    ) -> PipelineNodeRun:
        if node.attempt < spec_node.retry.max_attempts:
            return node.model_copy(
                update={
                    "attempt": node.attempt + 1,
                    "status": NodeStatus.PENDING,
                    "child_run_id": None,
                    "error": None,
                }
            )
        return node.model_copy(
            update={
                "status": NodeStatus.FAILED,
                "error": error,
                "cost_usd": cost_usd,
                "finished_at": self._clock(),
            }
        )

    def _publish(self, context: PipelineContext, node: PipelineNodeRun) -> None:
        context.outputs[node.node_id] = {
            "output": node.output,
            "status": node.status.value,
            "cost": node.cost_usd,
        }

    # ------------------------------------------------------------------ #
    # Fan-out / fan-in
    # ------------------------------------------------------------------ #
    def _fan_out(
        self,
        spec_node: PipelineNodeSpec,
        spec: PipelineSpec,
        context: PipelineContext,
        node: PipelineNodeRun,
        header: PipelineRunHeader,
        ctx: TenantContext,
    ) -> PipelineNodeRun:
        template = spec.node(spec_node.node or "")
        if template is None or template.workload is None:
            return self._failed(spec_node, node, "fan_out template node is missing")
        try:
            items = render_ref(spec_node.over or "", context)
        except HandoffError as exc:
            return self._failed(spec_node, node, str(exc))
        if not isinstance(items, list):
            return self._failed(spec_node, node, "fan_out 'over' must resolve to a list")
        instances = node.instances or [
            FanOutInstance(index=index, item=item) for index, item in enumerate(items)
        ]
        for index, instance in enumerate(instances):
            if instance.status in (NodeStatus.COMPLETED, NodeStatus.FAILED):
                continue
            if instance.child_run_id is None:
                local = context.model_copy(deep=True)
                local.variables[spec_node.as_name or "item"] = instance.item
                try:
                    mapped = map_inputs(template.inputs, local)
                except HandoffError as exc:
                    instances[index] = instance.model_copy(
                        update={"status": NodeStatus.FAILED, "error": str(exc)}
                    )
                    continue
                origin = PipelineOrigin(
                    pipeline_run_id=node.pipeline_run_id,
                    pipeline_id=node.pipeline_id,
                    node_id=spec_node.id,
                    parent_run_id=header.parent_run_id,
                    attempt=node.attempt,
                )
                child = self._executor.submit(
                    workload=template.workload,
                    inputs=mapped,
                    context=AdmissionContext.STAGING,
                    origin=origin,
                    ctx=ctx,
                )
                instance = instance.model_copy(
                    update={"status": NodeStatus.RUNNING, "child_run_id": child}
                )
                instances[index] = instance
            if instance.status is NodeStatus.RUNNING and instance.child_run_id is not None:
                result = self._executor.result(instance.child_run_id, ctx=ctx)
                if result is not None:
                    instances[index] = instance.model_copy(
                        update={
                            "status": result.status,
                            "output": result.output,
                            "cost_usd": result.cost_usd,
                            "error": result.error,
                        }
                    )
        return self._fan_out_result(spec_node, node, instances, context)

    def _reconcile_fan_out(
        self,
        spec_node: PipelineNodeSpec,
        node: PipelineNodeRun,
        context: PipelineContext,
        ctx: TenantContext,
    ) -> PipelineNodeRun | None:
        changed = False
        instances = []
        for instance in node.instances:
            if instance.status is NodeStatus.RUNNING and instance.child_run_id is not None:
                result = self._executor.result(instance.child_run_id, ctx=ctx)
                if result is not None:
                    instance = instance.model_copy(
                        update={
                            "status": result.status,
                            "output": result.output,
                            "cost_usd": result.cost_usd,
                            "error": result.error,
                        }
                    )
                    changed = True
            instances.append(instance)
        if not changed:
            return None
        return self._fan_out_result(spec_node, node, instances, context)

    def _fan_out_result(
        self,
        spec_node: PipelineNodeSpec,
        node: PipelineNodeRun,
        instances: list[FanOutInstance],
        context: PipelineContext,
    ) -> PipelineNodeRun:
        cost = sum(instance.cost_usd for instance in instances)
        if any(instance.status is NodeStatus.FAILED for instance in instances):
            status = NodeStatus.FAILED
            error = next(
                instance.error for instance in instances if instance.status is NodeStatus.FAILED
            )
        elif all(instance.status is NodeStatus.COMPLETED for instance in instances):
            status = NodeStatus.COMPLETED
            error = None
        else:
            status = NodeStatus.RUNNING
            error = None
        output: dict[str, JsonValue] = {}
        for instance in instances:
            key = (
                str(instance.index)
                if isinstance(instance.item, (dict, list))
                else str(instance.item)
            )
            output[key] = instance.output
        result = node.model_copy(
            update={
                "status": status,
                "instances": instances,
                "cost_usd": cost,
                "output": output if status is NodeStatus.COMPLETED else node.output,
                "error": error,
                "finished_at": (
                    self._clock()
                    if status in (NodeStatus.COMPLETED, NodeStatus.FAILED)
                    else None
                ),
            }
        )
        if status is NodeStatus.COMPLETED:
            self._publish(context, result)
        return result

    def _fan_in(
        self,
        spec_node: PipelineNodeSpec,
        context: PipelineContext,
        node: PipelineNodeRun,
    ) -> PipelineNodeRun:
        try:
            outputs = [context.outputs[source]["output"] for source in spec_node.from_nodes]
        except KeyError:
            return node.model_copy(
                update={
                    "status": NodeStatus.FAILED,
                    "error": "fan_in source output is missing",
                    "finished_at": self._clock(),
                }
            )
        reduced = _reduce(spec_node.reducer, outputs)
        result = node.model_copy(
            update={
                "status": NodeStatus.COMPLETED,
                "output": reduced,
                "finished_at": self._clock(),
            }
        )
        self._publish(context, result)
        return result

    # ------------------------------------------------------------------ #
    # Helpers
    # ------------------------------------------------------------------ #
    def _node_run(
        self, header: PipelineRunHeader, spec_node: PipelineNodeSpec
    ) -> PipelineNodeRun:
        return PipelineNodeRun(
            pipeline_run_id=header.pipeline_run_id,
            pipeline_id=header.pipeline_id,
            node_id=spec_node.id,
            tenant_id=header.tenant_id,
            started_at=self._clock(),
        )

    def _latest_nodes(
        self, pipeline_run_id: str, ctx: TenantContext
    ) -> dict[str, PipelineNodeRun]:
        latest: dict[str, PipelineNodeRun] = {}
        for node in self._store.list_node_runs(pipeline_run_id, ctx=ctx):
            current = latest.get(node.node_id)
            if current is None or node.attempt > current.attempt:
                latest[node.node_id] = node
        return latest

    def _projected_cost(self, spec_node: PipelineNodeSpec) -> float:
        if spec_node.budget is not None:
            return spec_node.budget.usd
        if spec_node.workload is not None:
            return self._budget_lookup(spec_node.workload) or 0.0
        return 0.0

    def _budget_exceeded(
        self, header: PipelineRunHeader
    ) -> tuple[PipelineState, str] | None:
        if header.budget_usd <= 0 or header.spent_usd <= header.budget_usd:
            return None
        return PipelineState.PAUSED, "pipeline budget exhausted"

    def _derive_state(
        self, spec: PipelineSpec, nodes: dict[str, PipelineNodeRun]
    ) -> PipelineState:
        managed = _fan_out_templates(spec)
        required = [node for node in spec.nodes if node.id not in managed]
        statuses = [nodes[node.id].status for node in required if node.id in nodes]
        all_statuses = [node.status for node in nodes.values()]
        if any(
            status in (NodeStatus.FAILED, NodeStatus.SKIPPED) for status in all_statuses
        ):
            return PipelineState.FAILED
        if any(status is NodeStatus.WAITING_APPROVAL for status in all_statuses):
            return PipelineState.PAUSED
        if len(statuses) == len(required) and all(
            status is NodeStatus.COMPLETED for status in statuses
        ):
            return PipelineState.COMPLETED
        return PipelineState.RUNNING

    def _skip_after_fail_fast(
        self,
        spec: PipelineSpec,
        nodes: dict[str, PipelineNodeRun],
        header: PipelineRunHeader,
        ctx: TenantContext,
    ) -> None:
        def _fails_fast(node_id: str, node: PipelineNodeRun) -> bool:
            spec_node = spec.node(node_id)
            return node.status is NodeStatus.FAILED and (
                spec_node is None or spec_node.on_failure is OnFailure.FAIL_FAST
            )

        failed_fast = any(_fails_fast(node_id, node) for node_id, node in nodes.items())
        if not failed_fast:
            return
        for spec_node in spec.nodes:
            current = nodes.get(spec_node.id)
            if (
                current is not None
                and current.status is NodeStatus.RUNNING
                and current.child_run_id is not None
            ):
                self._executor.cancel(current.child_run_id, ctx=ctx)
            if current is not None and current.status in (
                NodeStatus.COMPLETED,
                NodeStatus.FAILED,
                NodeStatus.SKIPPED,
            ):
                continue
            skipped = (current or self._node_run(header, spec_node)).model_copy(
                update={"status": NodeStatus.SKIPPED, "error": "fail_fast"}
            )
            nodes[spec_node.id] = skipped
            self._store.save_node_run(skipped, ctx=ctx)


def _spent(nodes: dict[str, PipelineNodeRun]) -> float:
    return sum(node.cost_usd for node in nodes.values())


def _fan_out_templates(spec: PipelineSpec) -> set[str]:
    return {node.node for node in spec.nodes if node.kind is NodeKind.FAN_OUT and node.node}


def _topological_order(spec: PipelineSpec) -> list[PipelineNodeSpec]:
    indegree = {node.id: 0 for node in spec.nodes}
    adjacency: dict[str, list[str]] = {node.id: [] for node in spec.nodes}
    for node in spec.nodes:
        for dep in spec.dependencies(node.id):
            adjacency[dep].append(node.id)
            indegree[node.id] += 1
    ready = [node_id for node_id in indegree if indegree[node_id] == 0]
    order: list[str] = []
    while ready:
        current = ready.pop(0)
        order.append(current)
        for neighbour in adjacency[current]:
            indegree[neighbour] -= 1
            if indegree[neighbour] == 0:
                ready.append(neighbour)
    return [node for node_id in order for node in spec.nodes if node.id == node_id]


def _reduce(reducer: Reducer | None, outputs: list[JsonValue]) -> JsonValue:
    if reducer is Reducer.JSON_MERGE:
        merged: dict[str, JsonValue] = {}
        for output in outputs:
            if isinstance(output, dict):
                merged.update(output)
            elif isinstance(output, list):
                for index, item in enumerate(output):
                    merged[str(index)] = item
        return merged
    if reducer is Reducer.CONCAT:
        concatenated: list[JsonValue] = []
        for output in outputs:
            if isinstance(output, list):
                concatenated.extend(output)
        return concatenated
    if reducer is Reducer.SUM:
        return sum(output for output in outputs if isinstance(output, (int, float)))
    if reducer is Reducer.FIRST_SUCCESS:
        for output in outputs:
            if output is not None:
                return output
        return None
    return outputs[0] if outputs else None
