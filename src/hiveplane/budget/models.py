"""Cost attribution and budget snapshot models (D5, D14)."""

from __future__ import annotations

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from hiveplane.tenancy.context import DEFAULT_TENANT_ID


class CostAttribution(BaseModel):
    """A priced usage event attributed to a run, workload, and team."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    workload: str = Field(min_length=1)
    team: str | None = None
    model_identity: str | None = None
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)
    timestamp: AwareDatetime
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    attribution_key: str | None = None


class BudgetSnapshot(BaseModel):
    """Current spend for a workload and team over a day."""

    model_config = ConfigDict(extra="forbid")

    workload: str
    team: str | None = None
    day: str
    run_usd: float = Field(ge=0.0)
    day_usd: float = Field(ge=0.0)
    team_usd: float = Field(ge=0.0)


class SpendByWorkload(BaseModel):
    """Attributed spend rolled up for a workload."""

    model_config = ConfigDict(extra="forbid")

    workload: str
    team: str | None = None
    total_usd: float = Field(ge=0.0)
    run_count: int = Field(ge=0)


class SpendByTeam(BaseModel):
    """Attributed spend rolled up for a team."""

    model_config = ConfigDict(extra="forbid")

    team: str
    total_usd: float = Field(ge=0.0)
    run_count: int = Field(ge=0)


class SpendSummary(BaseModel):
    """Attributed spend for the fleet, by workload and by team."""

    model_config = ConfigDict(extra="forbid")

    by_workload: list[SpendByWorkload] = Field(default_factory=list)
    by_team: list[SpendByTeam] = Field(default_factory=list)
