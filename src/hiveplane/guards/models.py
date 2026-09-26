"""Typed records for runtime guards (M41)."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

from hiveplane.tenancy.context import DEFAULT_TENANT_ID


class GuardKind(StrEnum):
    """Which runtime guard produced an event."""

    CONTEXT = "context"
    VELOCITY = "velocity"
    RETRY = "retry"
    BREAKER = "breaker"


class GuardAction(StrEnum):
    """What a guard decided to do."""

    PAUSE = "pause"
    DENY = "deny"
    THROTTLE = "throttle"
    ALLOW = "allow"


class RetryPolicy(BaseModel):
    """Per-workload/tool retry policy with exponential backoff and jitter."""

    model_config = ConfigDict(extra="forbid")

    max_attempts: int = Field(default=3, ge=1)
    base_ms: int = Field(default=500, gt=0)
    max_ms: int = Field(default=30000, gt=0)
    jitter: Literal["none", "full"] = "full"


class GuardEvent(BaseModel):
    """A recorded guard activation, shaped like a policy decision (M41-07)."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1)
    guard: GuardKind
    action: GuardAction
    reason: str = Field(min_length=1)
    rule_id: str = Field(min_length=1)
    workload_id: str = Field(min_length=1)
    run_id: str | None = None
    tool_id: str | None = None
    observed: dict[str, JsonValue] = Field(default_factory=dict)
    threshold: dict[str, JsonValue] = Field(default_factory=dict)
    created_at: AwareDatetime
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
