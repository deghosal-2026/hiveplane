"""Agent-as-tool API (M30-04..M30-06)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from hiveplane.agent_tools.engine import AgentToolInvoker
from hiveplane.agent_tools.models import (
    AgentTool,
    AgentToolInvocation,
    AgentToolInvocationRequest,
)
from hiveplane.agent_tools.registry import AgentToolRegistry
from hiveplane.api.deps import (
    get_agent_tool_invoker,
    get_agent_tool_registry,
    get_tenant_context,
)
from hiveplane.core.run import AdmissionContext
from hiveplane.tenancy import TenantContext

router = APIRouter(tags=["agent-tools"])

RegistryDep = Annotated[AgentToolRegistry, Depends(get_agent_tool_registry)]
InvokerDep = Annotated[AgentToolInvoker, Depends(get_agent_tool_invoker)]
TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]


@router.get("/agent-tools", response_model=list[AgentTool])
def list_agent_tools(
    registry: RegistryDep,
    ctx: TenantDep,
    context: str = "staging",
) -> list[AgentTool]:
    """List certified workloads exposed as callable tools."""
    try:
        resolved = AdmissionContext(context)
    except ValueError as exc:
        raise HTTPException(
            status.HTTP_422_UNPROCESSABLE_CONTENT, f"invalid context {context!r}"
        ) from exc
    return registry.list_tools(resolved, ctx=ctx)


@router.post(
    "/agent-tools/{tool_id}/invoke",
    response_model=AgentToolInvocation,
    responses={403: {"description": "nested call refused"}},
)
def invoke_agent_tool(
    tool_id: str,
    request: AgentToolInvocationRequest,
    invoker: InvokerDep,
    ctx: TenantDep,
) -> AgentToolInvocation:
    """Invoke a certified workload as a nested tool, or refuse."""
    invocation = invoker.invoke(
        tool_id,
        caller_run_id=request.caller_run_id,
        task=request.task,
        context=request.context,
        depth=request.depth,
        chain=request.chain,
        budget_remaining_usd=request.budget_remaining_usd,
        caller_cost_usd=request.caller_cost_usd,
        ctx=ctx,
    )
    if not invocation.allowed:
        raise HTTPException(status.HTTP_403_FORBIDDEN, invocation.reason or "refused")
    return invocation
