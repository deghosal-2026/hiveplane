"""Models for the `ask` operator copilot (M53, D36)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, JsonValue


class AskIntent(StrEnum):
    """The resolved intent of a natural-language operator question."""

    RUN_FAILURE = "run_failure"
    TEAM_SPEND = "team_spend"
    APPROVAL_ATTRIBUTION = "approval_attribution"
    WORKLOAD_HEALTH = "workload_health"
    FLEET_STATUS = "fleet_status"
    MUTATION = "mutation"
    UNKNOWN = "unknown"


class AskAnswer(BaseModel):
    """An attributed, read-only answer to an operator question."""

    model_config = ConfigDict(extra="forbid")

    question: str = Field(min_length=1)
    intent: AskIntent
    answer: str = Field(min_length=1)
    evidence: dict[str, JsonValue] = Field(default_factory=dict)
    citations: list[str] = Field(default_factory=list)
    read_only: bool = True
    requires_confirmation: bool = False
    tenant_id: str = Field(min_length=1)
    attributed_to: str | None = None
