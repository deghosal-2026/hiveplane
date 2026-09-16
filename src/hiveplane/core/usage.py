"""Usage and cost reporting models (D14)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class BudgetLevel(StrEnum):
    """The budget scope a check applies to."""

    RUN = "run"
    DAY = "day"
    TEAM = "team"


class BudgetCheck(BaseModel):
    """The result of a budget check at run, day, or team scope."""

    model_config = ConfigDict(extra="forbid")

    allowed: bool
    level: BudgetLevel
    limit_usd: float = Field(ge=0.0)
    spent_usd: float = Field(ge=0.0)
    remaining_usd: float = Field(ge=0.0)
    reason: str | None = None


class UsageReport(BaseModel):
    """Token, tool-call, and cost usage reported for a run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)
    timestamp: AwareDatetime
    model_identity: str | None = None

    @property
    def total_tokens(self) -> int:
        """Total tokens consumed (input + output)."""
        return self.input_tokens + self.output_tokens


class BudgetOutcome(BaseModel):
    """A usage record's effect on budget: the check and the priced cost."""

    model_config = ConfigDict(extra="forbid")

    check: BudgetCheck
    cost_usd: float = Field(ge=0.0)
