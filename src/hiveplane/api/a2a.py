"""Agent2Agent (A2A) interop API (M30-07, stretch)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, Request

from hiveplane.a2a import A2AAdapter, A2AAgentCard, A2ATaskRequest, A2ATaskResult
from hiveplane.api.deps import get_a2a_adapter, get_tenant_context
from hiveplane.tenancy import TenantContext

router = APIRouter(tags=["a2a"])

AdapterDep = Annotated[A2AAdapter, Depends(get_a2a_adapter)]
TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]


def _base_url(request: Request) -> str:
    return str(request.base_url).rstrip("/")


@router.get("/a2a/agents", response_model=list[A2AAgentCard])
def list_a2a_agents(
    request: Request, adapter: AdapterDep, ctx: TenantDep
) -> list[A2AAgentCard]:
    """Expose certified workloads as A2A agent cards."""
    return adapter.agent_cards(_base_url(request), ctx=ctx)


@router.post("/a2a/tasks", response_model=A2ATaskResult)
def submit_a2a_task(
    task: A2ATaskRequest, adapter: AdapterDep, ctx: TenantDep
) -> A2ATaskResult:
    """Handle an inbound A2A task by routing it to a certified workload."""
    return adapter.handle_task(task, ctx=ctx)
