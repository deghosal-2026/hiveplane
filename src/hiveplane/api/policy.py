"""Policy evaluation and policy pack API."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, HTTPException, status
from pydantic import BaseModel, ConfigDict, Field

from hiveplane.api.deps import (
    get_kill_switch,
    get_policy_engine,
    get_policy_pack_registry,
    get_policy_pack_store,
    get_tenant_context,
    require_permission,
)
from hiveplane.auth.models import OperatorIdentity, Permission
from hiveplane.core.decision import PolicyContext, PolicyDecision
from hiveplane.policy.engine import PolicyEngine
from hiveplane.policy.kill_switch import KillSwitch, KillSwitchRecord
from hiveplane.policy.models import PolicyEvaluationRequest, PolicyPack
from hiveplane.policy.pack_registry import PolicyPackRegistry
from hiveplane.policy.packs import PolicyPackStore
from hiveplane.tenancy import TenantContext

router = APIRouter(tags=["policy"])

EngineDep = Annotated[PolicyEngine, Depends(get_policy_engine)]
PackStoreDep = Annotated[PolicyPackStore, Depends(get_policy_pack_store)]
KillSwitchDep = Annotated[KillSwitch, Depends(get_kill_switch)]
TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]


class KillSwitchRequest(BaseModel):
    """Request body to disable or re-enable a tool."""

    model_config = ConfigDict(extra="forbid")

    actor: str = Field(min_length=1)
    reason: str | None = None


@router.post("/policy/evaluate", response_model=PolicyDecision)
def evaluate_policy(payload: PolicyEvaluationRequest, engine: EngineDep) -> PolicyDecision:
    """Evaluate policy for a context (real or dry-run) with an explainable decision."""
    data = payload.model_dump()
    dry_run = bool(data.pop("dry_run", False))
    return engine.evaluate(PolicyContext(**data), dry_run=dry_run)


@router.get("/policy-packs", response_model=list[PolicyPack])
def list_policy_packs(store: PackStoreDep, ctx: TenantDep) -> list[PolicyPack]:
    """List the acting tenant's registered policy packs."""
    return store.list_packs(ctx=ctx)


@router.post("/policy-packs", response_model=PolicyPack, status_code=status.HTTP_201_CREATED)
def register_policy_pack(pack: PolicyPack, store: PackStoreDep, ctx: TenantDep) -> PolicyPack:
    """Register a team policy pack in the acting tenant."""
    store.save(pack, ctx=ctx)
    return pack


@router.get("/tools/disabled", response_model=list[KillSwitchRecord])
def list_disabled_tools(kill_switch: KillSwitchDep) -> list[KillSwitchRecord]:
    """List tools currently disabled by the kill switch."""
    return kill_switch.records()


@router.post("/tools/{tool_id}/disable", response_model=KillSwitchRecord)
def disable_tool(
    tool_id: str,
    payload: KillSwitchRequest,
    kill_switch: KillSwitchDep,
    _: Annotated[OperatorIdentity, Depends(require_permission(Permission.KILL_SWITCH))],
) -> KillSwitchRecord:
    """Disable a tool fleet-wide (fail-closed at the tool-call boundary)."""
    return kill_switch.disable(tool_id, actor=payload.actor, reason=payload.reason)


@router.post("/tools/{tool_id}/enable", response_model=KillSwitchRecord)
def enable_tool(
    tool_id: str,
    payload: KillSwitchRequest,
    kill_switch: KillSwitchDep,
    _: Annotated[OperatorIdentity, Depends(require_permission(Permission.KILL_SWITCH))],
) -> KillSwitchRecord:
    """Re-enable a disabled tool."""
    return kill_switch.enable(tool_id, actor=payload.actor)


class ApplyPackRequest(BaseModel):
    """Request body to apply a published policy pack to a team."""

    model_config = ConfigDict(extra="forbid")

    team: str = Field(min_length=1)


@router.post("/policy-packs/{name}/apply", response_model=list[PolicyPack])
def apply_policy_pack(
    name: str,
    payload: ApplyPackRequest,
    registry: Annotated[PolicyPackRegistry, Depends(get_policy_pack_registry)],
    ctx: TenantDep,
) -> list[PolicyPack]:
    """Pin a published pack (and its inherited chain) to a team."""
    try:
        return registry.apply(name, team=payload.team, ctx=ctx)
    except KeyError as exc:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND, detail=f"policy pack {name!r} not found"
        ) from exc
