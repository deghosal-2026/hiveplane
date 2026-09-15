"""Usage and cost reporting models (D14)."""

from __future__ import annotations

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class UsageReport(BaseModel):
    """Token, tool-call, and cost usage reported for a run."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    input_tokens: int = Field(ge=0)
    output_tokens: int = Field(ge=0)
    tool_calls: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)
    timestamp: AwareDatetime

    @property
    def total_tokens(self) -> int:
        """Total tokens consumed (input + output)."""
        return self.input_tokens + self.output_tokens
