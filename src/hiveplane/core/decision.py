"""Policy decision models (DD-03)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class DecisionOutcome(StrEnum):
    """The outcome of evaluating policy for a run or tool call."""

    ALLOW = "allow"
    DENY = "deny"
    ESCALATE = "escalate"


class ActionClass(StrEnum):
    """Known action classes used by policy and approvals."""

    READ_ONLY = "read_only"
    DESTRUCTIVE = "destructive"
    PRODUCTION_WRITE = "production_write"
    HIGH_SPEND = "high_spend"


class PolicyDecision(BaseModel):
    """A policy evaluation with its reason and the rule that produced it."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    outcome: DecisionOutcome
    reason: str = Field(min_length=1)
    rule: str = Field(min_length=1)
    action_class: ActionClass | None = None
    timestamp: AwareDatetime
