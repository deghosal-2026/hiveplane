"""Replay API: frame replay, run diff, fork, and A/B replay (M60-06)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends, status
from pydantic import BaseModel, ConfigDict, Field, JsonValue

from hiveplane.api.deps import get_replay_service, get_tenant_context, require_permission
from hiveplane.auth.models import OperatorIdentity, Permission
from hiveplane.replay.models import (
    ABReplayResult,
    ForkResult,
    ReplayFrameSet,
    ReplayMode,
    ReplayRecord,
    RunDiff,
)
from hiveplane.replay.service import ReplayService
from hiveplane.tenancy import TenantContext

router = APIRouter(tags=["replay"])

ReplayDep = Annotated[ReplayService, Depends(get_replay_service)]
TenantDep = Annotated[TenantContext, Depends(get_tenant_context)]
FleetReader = Annotated[OperatorIdentity, Depends(require_permission(Permission.FLEET_READ))]
ReplayAdmin = Annotated[
    OperatorIdentity, Depends(require_permission(Permission.REPLAY_MANAGE))
]


class ForkRequest(BaseModel):
    """Request body to fork a run with optional state edits."""

    model_config = ConfigDict(extra="forbid")

    edits: dict[str, JsonValue] = Field(default_factory=dict)
    fork_point: int | None = Field(default=None, ge=0)
    side_effects: bool = False


class ABReplayRequest(BaseModel):
    """Request body to A/B replay two workloads on identical input."""

    model_config = ConfigDict(extra="forbid")

    source_run_id: str = Field(min_length=1)
    workload_a: str = Field(min_length=1)
    workload_b: str = Field(min_length=1)
    side_effects: bool = False


@router.post("/replay/ab", response_model=ABReplayResult, status_code=status.HTTP_201_CREATED)
def ab_replay(
    request: ABReplayRequest,
    service: ReplayDep,
    identity: ReplayAdmin,
    tenants: TenantDep,
) -> ABReplayResult:
    """Run two workloads on identical input and return a side-by-side diff."""
    return service.ab(
        request.source_run_id,
        workload_a=request.workload_a,
        workload_b=request.workload_b,
        side_effects=request.side_effects,
        actor=identity.operator_id,
        ctx=tenants,
    )


@router.get("/replays", response_model=list[ReplayRecord])
def list_replays(
    service: ReplayDep,
    _: FleetReader,
    tenants: TenantDep,
    source_run_id: str | None = None,
    mode: ReplayMode | None = None,
) -> list[ReplayRecord]:
    """List recorded replay/fork/A-B operations."""
    return service.list(source_run_id=source_run_id, mode=mode, ctx=tenants)


@router.get("/replay/diff", response_model=RunDiff)
def run_diff(
    service: ReplayDep,
    _: FleetReader,
    tenants: TenantDep,
    run_a: str,
    run_b: str,
) -> RunDiff:
    """Compare two runs by state, calls, cost, and outcome."""
    return service.diff(run_a, run_b, ctx=tenants)


@router.post("/replay/{run_id}", response_model=ReplayFrameSet)
def replay_run(
    run_id: str,
    service: ReplayDep,
    _: FleetReader,
    tenants: TenantDep,
) -> ReplayFrameSet:
    """Reconstruct a run frame-by-frame (side-effect free)."""
    return service.replay(run_id, ctx=tenants)


@router.post("/runs/{run_id}/fork", response_model=ForkResult, status_code=status.HTTP_201_CREATED)
def fork_run(
    run_id: str,
    request: ForkRequest,
    service: ReplayDep,
    identity: ReplayAdmin,
    tenants: TenantDep,
) -> ForkResult:
    """Copy a run's task (with edits) into a new read-only run and re-run it."""
    return service.fork(
        run_id,
        edits=request.edits,
        fork_point=request.fork_point,
        side_effects=request.side_effects,
        actor=identity.operator_id,
        ctx=tenants,
    )
