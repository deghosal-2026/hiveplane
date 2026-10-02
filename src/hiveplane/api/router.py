"""Smart task router API (M30-01..M30-03)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status

from hiveplane.api.deps import get_router_engine, get_router_store, get_tenant_context
from hiveplane.router.engine import RouterEngine
from hiveplane.router.models import RouteDecision, RouteRequest
from hiveplane.router.store import RouterStore
from hiveplane.tenancy import TenantContext

router = APIRouter(tags=["router"])

EngineDep = Annotated[RouterEngine, Depends(get_router_engine)]
StoreDep = Annotated[RouterStore, Depends(get_router_store)]
TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]


@router.post("/route", response_model=RouteDecision)
def route_task(request: RouteRequest, engine: EngineDep, ctx: TenantDep) -> RouteDecision:
    """Route a plain-language task to the best certified workload, or refuse."""
    return engine.route(request.task, context=request.context, ctx=ctx)


@router.get("/routes", response_model=list[RouteDecision])
def list_routes(store: StoreDep, ctx: TenantDep) -> list[RouteDecision]:
    """List recorded router decisions (most recent first)."""
    decisions = store.list_decisions(ctx=ctx)
    decisions.reverse()
    return decisions


@router.get("/routes/{decision_id}", response_model=RouteDecision)
def get_route(decision_id: str, store: StoreDep, ctx: TenantDep) -> RouteDecision:
    """Return one recorded router decision."""
    decision = store.get_decision(decision_id, ctx=ctx)
    if decision is None:
        raise HTTPException(status.HTTP_404_NOT_FOUND, f"route {decision_id!r} not found")
    return decision
