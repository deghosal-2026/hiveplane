"""Run event records (D2, D7)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from hiveplane.core.run import RunState


class EventType(StrEnum):
    """Kinds of events recorded against a run."""

    ADMISSION = "admission"
    STATE_CHANGE = "state_change"
    POLICY_DECISION = "policy_decision"
    TOOL_CALL = "tool_call"
    USAGE = "usage"
    OPERATOR_ACTION = "operator_action"
    SANDBOX = "sandbox"
    RECOVERY = "recovery"
    GUARD = "guard"


class RunEvent(BaseModel):
    """An attributed, ordered event in a run's history."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    sequence: int = Field(ge=0)
    type: EventType
    actor: str = Field(min_length=1)
    timestamp: AwareDatetime
    from_state: RunState | None = None
    to_state: RunState | None = None
    detail: str | None = None
