"""Cost attribution and budget snapshot models (D5, D14)."""

from __future__ import annotations

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


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


class BudgetSnapshot(BaseModel):
    """Current spend for a workload and team over a day."""

    model_config = ConfigDict(extra="forbid")

    workload: str
    team: str | None = None
    day: str
    run_usd: float = Field(ge=0.0)
    day_usd: float = Field(ge=0.0)
    team_usd: float = Field(ge=0.0)
