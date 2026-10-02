"""Chaos/game-day drill models (M48-04..M48-06)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class ChaosError(Exception):
    """Base class for chaos-drill failures."""


class DrillRefusedError(ChaosError):
    """Raised when guardrails refuse to run a drill."""

    def __init__(self, reason: str) -> None:
        super().__init__(reason)
        self.reason = reason


class DrillKind(StrEnum):
    """A seeded failure the plane can inject."""

    KILL_WORKER = "kill-worker"
    REVOKE_CERT = "revoke-cert"
    EXHAUST_BUDGET = "exhaust-budget"
    INJECT_TOOL_FAILURE = "inject-tool-failure"


class DrillScope(StrEnum):
    """How wide a drill may act."""

    SANDBOX = "sandbox"
    TENANT = "tenant"


class DrillVerdict(StrEnum):
    """The outcome of a drill."""

    PASS = "pass"
    FAIL = "fail"
    REFUSED = "refused"


class DrillRequest(BaseModel):
    """A request to run a seeded failure against a scope."""

    model_config = ConfigDict(extra="forbid")

    kind: DrillKind
    scope: DrillScope = DrillScope.SANDBOX
    scope_ref: str = Field(default="sandbox", min_length=1, max_length=253)
    seed: int | None = None
    production: bool = False
    allow_production: bool = False
    requested_by: str = Field(default="cli", min_length=1, max_length=253)


class DrillReport(BaseModel):
    """What was injected, what the plane did, and whether it recovered."""

    model_config = ConfigDict(extra="forbid")

    drill_id: str = Field(min_length=1)
    kind: DrillKind
    scope: DrillScope
    scope_ref: str
    injected: str = Field(min_length=1)
    observed: str = Field(min_length=1)
    verdict: DrillVerdict
    started_at: AwareDatetime
    finished_at: AwareDatetime
    notes: list[str] = Field(default_factory=list)
