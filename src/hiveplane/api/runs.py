"""Run execution API: submission, inspection, and intervention."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from hiveplane.api.deps import get_approval_service, get_run_service, get_tool_gateway
from hiveplane.core.event import RunEvent
from hiveplane.core.run import Run, RunState
from hiveplane.core.usage import UsageReport
from hiveplane.execution.models import InterventionAction, RunSubmission
from hiveplane.execution.service import RunService
from hiveplane.execution.story import RunStory
from hiveplane.execution.tools import ToolCallRequest, ToolCallResult, ToolGateway
from hiveplane.policy.approvals import ApprovalService

router = APIRouter(tags=["runs"])

ServiceDep = Annotated[RunService, Depends(get_run_service)]
GatewayDep = Annotated[ToolGateway, Depends(get_tool_gateway)]
ApprovalDep = Annotated[ApprovalService, Depends(get_approval_service)]


@router.post("/runs", response_model=Run, status_code=status.HTTP_201_CREATED)
def submit_run(payload: RunSubmission, service: ServiceDep) -> Run:
    """Submit a run for admission."""
    return service.submit(
        workload=payload.workload,
        caller=payload.caller,
        context=payload.context,
        task=payload.task,
        model_identity=payload.model_identity,
    )


@router.get("/runs", response_model=list[Run])
def list_runs(
    service: ServiceDep,
    workload: str | None = None,
    state: RunState | None = None,
) -> list[Run]:
    """List runs, optionally filtered."""
    return service.list_runs(workload=workload, state=state)


@router.get("/runs/{run_id}", response_model=Run)
def get_run(run_id: str, service: ServiceDep) -> Run:
    """Return a run by id."""
    return service.get(run_id)


@router.get("/runs/{run_id}/events", response_model=list[RunEvent])
def list_events(run_id: str, service: ServiceDep) -> list[RunEvent]:
    """Return a run's ordered event log."""
    return service.events(run_id)


@router.get("/runs/{run_id}/usage", response_model=list[UsageReport])
def list_usage(run_id: str, service: ServiceDep) -> list[UsageReport]:
    """Return a run's usage reports."""
    return service.usage(run_id)


@router.get("/runs/{run_id}/story", response_model=RunStory)
def get_run_story(run_id: str, service: ServiceDep, approvals: ApprovalDep) -> RunStory:
    """Return a run's execution story, with approvals and its trace link."""
    return service.story(run_id, approvals=approvals.list(run_id=run_id))


@router.post("/runs/{run_id}/pause", response_model=Run)
def pause_run(run_id: str, service: ServiceDep) -> Run:
    """Pause a running run."""
    return service.intervene(run_id, InterventionAction.PAUSE, actor="api")


@router.post("/runs/{run_id}/resume", response_model=Run)
def resume_run(run_id: str, service: ServiceDep) -> Run:
    """Resume a paused run."""
    return service.intervene(run_id, InterventionAction.RESUME, actor="api")


@router.post("/runs/{run_id}/stop", response_model=Run)
def stop_run(run_id: str, service: ServiceDep) -> Run:
    """Stop a run immediately."""
    return service.intervene(run_id, InterventionAction.STOP, actor="api")


@router.post("/runs/{run_id}/tool-calls", response_model=ToolCallResult)
def invoke_tool_call(
    run_id: str, payload: ToolCallRequest, gateway: GatewayDep
) -> ToolCallResult:
    """Authorize a tool call through policy, egress, and shaping."""
    return gateway.invoke(run_id, payload)


@router.post("/runs/{run_id}/start", response_model=Run)
def start_run(run_id: str, service: ServiceDep) -> Run:
    """Start a queued run (adapter pickup or operator start)."""
    return service.start(run_id, actor="api")
