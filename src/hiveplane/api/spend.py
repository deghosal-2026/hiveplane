"""Spend read API: attributed spend by workload and team (M22, #82)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from hiveplane.api.deps import get_budget_store, get_tenant_context
from hiveplane.budget.models import SpendSummary
from hiveplane.budget.store import BudgetStore
from hiveplane.budget.summary import summarize_spend
from hiveplane.tenancy import TenantContext

router = APIRouter(tags=["spend"])

StoreDep = Annotated[BudgetStore, Depends(get_budget_store)]
TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]


@router.get("/spend", response_model=SpendSummary)
def get_spend(store: StoreDep, ctx: TenantDep) -> SpendSummary:
    """Return the acting tenant's attributed spend by workload and by team."""
    return summarize_spend(store.list_attributions(ctx=ctx))
