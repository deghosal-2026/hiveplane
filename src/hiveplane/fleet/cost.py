"""Metering and cost-period models (M25-08, D21/D35).

A metering event is the append-only usage fact tagged with tenant, team,
workload, run, model, and cost. A cost period materializes day/week/month
buckets per team and workload, including cost-per-completed-task and an ROI
flag; periods are the durable query surface.
"""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from hiveplane.tenancy.context import DEFAULT_TENANT_ID


class CostPeriodKind(StrEnum):
    """The bucket size of a cost period."""

    DAY = "day"
    WEEK = "week"
    MONTH = "month"


class CostType(StrEnum):
    """The kind of spend a metering event represents."""

    LLM = "llm"
    TOOL = "tool"
    COMPUTE = "compute"
    STORAGE = "storage"


class MeteringEvent(BaseModel):
    """An append-only, attributed usage fact."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    team_id: str = Field(min_length=1, max_length=64)
    workload_id: str = Field(min_length=1, max_length=253)
    run_id: str | None = Field(default=None, max_length=64)
    pipeline_run_id: str | None = Field(default=None, max_length=64)
    cost_type: CostType
    model: str | None = Field(default=None, max_length=253)
    cost_usd: float = Field(ge=0.0)
    saved_usd: float = Field(default=0.0, ge=0.0)
    occurred_at: AwareDatetime


class CostPeriod(BaseModel):
    """A materialized cost bucket for a tenant/team/workload."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    team_id: str = Field(min_length=1, max_length=64)
    workload_id: str = Field(min_length=1, max_length=253)
    period: CostPeriodKind
    period_start: date
    total_cost_usd: float = Field(default=0.0, ge=0.0)
    cost_per_completed_task: float = Field(default=0.0, ge=0.0)
    cache_savings_usd: float = Field(default=0.0, ge=0.0)
    carry_in_usd: float = Field(default=0.0, ge=0.0)
    roi_flag: bool = False
