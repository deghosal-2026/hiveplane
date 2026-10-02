"""HA leader and chaos-drill API (M48)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends

from hiveplane.api.deps import (
    get_chaos_engine,
    get_leader_elector,
    require_permission,
)
from hiveplane.auth.models import OperatorIdentity, Permission
from hiveplane.chaos.engine import ChaosEngine
from hiveplane.chaos.models import DrillReport, DrillRequest
from hiveplane.ha.leader import LeaderElector

router = APIRouter(tags=["cluster"])

LeaderDep = Annotated[LeaderElector, Depends(get_leader_elector)]
ChaosDep = Annotated[ChaosEngine, Depends(get_chaos_engine)]
AdminOnly = Annotated[OperatorIdentity, Depends(require_permission(Permission.KILL_SWITCH))]


@router.get("/cluster/leader")
def cluster_leader(elector: LeaderDep) -> dict[str, object]:
    """Return the current leader and its fencing epoch."""
    lease = elector.current()
    return {
        "leader_id": None if lease is None else lease.leader_id,
        "epoch": 0 if lease is None else lease.epoch,
        "expires_at": None if lease is None else lease.expires_at.isoformat(),
    }


@router.post("/chaos/drills", response_model=DrillReport)
def run_drill(request: DrillRequest, engine: ChaosDep, _: AdminOnly) -> DrillReport:
    """Run a seeded chaos drill (production requires allow_production + admin)."""
    return engine.run(request)


@router.get("/chaos/drills", response_model=list[DrillReport])
def list_drills(engine: ChaosDep, _: AdminOnly) -> list[DrillReport]:
    """List chaos drill reports, newest first."""
    return engine.reports()
