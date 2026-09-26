"""Production adapters for the pipeline engine (M29-02).

``RunNodeExecutor`` submits each node as a child run through the run lifecycle
and reports its terminal result; ``ServiceApprovalGate`` reuses the approval
queue for per-step gates. Both keep pipelines on the same admission, budget, and
policy path as any other run.
"""

from __future__ import annotations

from typing import Any, Protocol

from pydantic import JsonValue

from hiveplane.core.run import AdmissionContext, PipelineOrigin, Run, RunState
from hiveplane.execution.models import InterventionAction
from hiveplane.pipelines.models import NodeResult, NodeStatus
from hiveplane.tenancy import DEFAULT_CONTEXT, TenantContext


class _RunService(Protocol):
    def submit(
        self,
        *,
        workload: str,
        caller: str,
        context: AdmissionContext,
        task: dict[str, JsonValue],
        pipeline_origin: PipelineOrigin | None,
        ctx: TenantContext,
    ) -> Run: ...

    def get(self, run_id: str, *, ctx: TenantContext) -> Run: ...

    def intervene(
        self,
        run_id: str,
        action: InterventionAction,
        *,
        actor: str,
        ctx: TenantContext,
    ) -> Run: ...


class RunNodeExecutor:
    """Submits pipeline nodes as child runs and polls their terminal state."""

    def __init__(self, run_service: _RunService) -> None:
        self._runs = run_service

    def submit(
        self,
        *,
        workload: str,
        inputs: dict[str, JsonValue],
        context: AdmissionContext,
        origin: PipelineOrigin,
        ctx: TenantContext = DEFAULT_CONTEXT,
    ) -> str:
        run = self._runs.submit(
            workload=workload,
            caller=f"pipeline:{origin.pipeline_run_id}",
            context=context,
            task=dict(inputs),
            pipeline_origin=origin,
            ctx=ctx,
        )
        return run.id

    def result(
        self, child_run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT
    ) -> NodeResult | None:
        run = self._runs.get(child_run_id, ctx=ctx)
        if run.state is RunState.COMPLETED:
            return NodeResult(
                status=NodeStatus.COMPLETED,
                output=run.result,
                cost_usd=run.cost_usd,
            )
        if run.state in (RunState.FAILED, RunState.CANCELLED):
            return NodeResult(
                status=NodeStatus.FAILED,
                cost_usd=run.cost_usd,
                error=run.failure_reason or run.state.value,
            )
        return None

    def cancel(self, child_run_id: str, *, ctx: TenantContext = DEFAULT_CONTEXT) -> None:
        self._runs.intervene(
            child_run_id, InterventionAction.STOP, actor="pipeline", ctx=ctx
        )


class _Approvals(Protocol):
    def request(
        self, *, run_id: str, workload: str, rule: str, reason: str
    ) -> Any: ...

    def get(self, approval_id: str) -> Any: ...


class ServiceApprovalGate:
    """Reuses the approval queue for per-step pipeline gates."""

    def __init__(self, approvals: _Approvals) -> None:
        self._approvals = approvals

    def request(self, *, run_id: str, workload: str, rule: str, reason: str) -> str:
        record = self._approvals.request(
            run_id=run_id, workload=workload, rule=rule, reason=reason
        )
        return str(record.approval_id)

    def status(self, approval_id: str) -> str:
        record = self._approvals.get(approval_id)
        status = record.status
        return str(getattr(status, "value", status))
