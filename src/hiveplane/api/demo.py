"""Demo profile seeding API (M59-07)."""

from __future__ import annotations

from typing import Annotated

from fastapi import APIRouter, Depends
from pydantic import BaseModel, ConfigDict, Field

from hiveplane.api.deps import get_demo_seeder, require_permission
from hiveplane.auth.models import OperatorIdentity, Permission
from hiveplane.demo.seed import DemoSeeder, DemoSeedReport

router = APIRouter(tags=["demo"])

SeederDep = Annotated[DemoSeeder, Depends(get_demo_seeder)]
DemoAdmin = Annotated[OperatorIdentity, Depends(require_permission(Permission.KEYS_MANAGE))]


class DemoSeedRequest(BaseModel):
    """Request body selecting a demo profile."""

    model_config = ConfigDict(extra="forbid")

    profile: str = Field(default="default", min_length=1, max_length=64)


@router.post("/demo/seed", response_model=DemoSeedReport)
def seed_demo(
    request: DemoSeedRequest, seeder: SeederDep, _: DemoAdmin
) -> DemoSeedReport:
    """Seed a screenshot-ready demo fleet (idempotent)."""
    return seeder.seed(profile=request.profile)
