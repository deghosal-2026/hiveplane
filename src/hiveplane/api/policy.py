"""Policy evaluation and policy pack API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status

from hiveplane.api.deps import get_policy_engine, get_policy_pack_store, get_tenant_context
from hiveplane.core.decision import PolicyContext, PolicyDecision
from hiveplane.policy.engine import PolicyEngine
from hiveplane.policy.models import PolicyEvaluationRequest, PolicyPack
from hiveplane.policy.packs import PolicyPackStore
from hiveplane.tenancy import TenantContext

router = APIRouter(tags=["policy"])

EngineDep = Annotated[PolicyEngine, Depends(get_policy_engine)]
PackStoreDep = Annotated[PolicyPackStore, Depends(get_policy_pack_store)]
TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]


@router.post("/policy/evaluate", response_model=PolicyDecision)
def evaluate_policy(payload: PolicyEvaluationRequest, engine: EngineDep) -> PolicyDecision:
    """Evaluate policy for a context and return an explainable decision."""
    return engine.evaluate(PolicyContext(**payload.model_dump()))


@router.get("/policy-packs", response_model=list[PolicyPack])
def list_policy_packs(store: PackStoreDep, ctx: TenantDep) -> list[PolicyPack]:
    """List the acting tenant's registered policy packs."""
    return store.list_packs(ctx=ctx)


@router.post("/policy-packs", response_model=PolicyPack, status_code=status.HTTP_201_CREATED)
def register_policy_pack(pack: PolicyPack, store: PackStoreDep, ctx: TenantDep) -> PolicyPack:
    """Register a team policy pack in the acting tenant."""
    store.save(pack, ctx=ctx)
    return pack
