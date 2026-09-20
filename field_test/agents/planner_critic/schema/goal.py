"""The Goal model (F-01): typed input to the planning loop.

A goal carries the constraints and risk posture under which the planner
must operate: a spend budget, environment assumptions, allowed tools, the
approval threshold (``risk_tolerance``), the mid-execution replan policy,
and an approval TTL for whole-plan drift control (F-18).

Per PRD §2.8:

.. code-block::

    Goal:
      id, description, constraints{budget, time, environment, tools[]},
      risk_tolerance{strict|balanced}, replan_policy{patch|restart|abort},
      approval_ttl (seconds, default ∞), metadata

Enums are strict: an unknown value fails validation with a
``ValidationError`` before any planning work happens.
"""

from __future__ import annotations

from datetime import timedelta
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class RiskTolerance(StrEnum):
    """Approval threshold posture for a goal.

    ``strict`` tolerates zero warnings; ``balanced`` tolerates acknowledged
    warnings; ``permissive`` tolerates warnings without acknowledgement.
    No blockers may ever remain under any posture (F-73).
    """

    STRICT = "strict"
    BALANCED = "balanced"
    PERMISSIVE = "permissive"


class ReplanPolicy(StrEnum):
    """Mid-execution policy when the re-gate finds a stale precondition."""

    PATCH = "patch"
    RESTART = "restart"
    ABORT = "abort"


class Budget(BaseModel):
    """Optional per-goal spend ceiling (F-06, PRD §2.4).

    All fields are optional — the loop enforces exactly the ceilings that are
    set. ``max_revisions`` bounds the revise loop; ``max_calls`` and
    ``max_tokens`` bound provider spend. Hitting any ceiling escalates rather
    than spending more.
    """

    model_config = ConfigDict(frozen=True)

    max_tokens: int | None = Field(default=None, ge=1)
    max_calls: int | None = Field(default=None, ge=1)
    max_revisions: int | None = Field(default=None, ge=1)


class Constraints(BaseModel):
    """The constraints block of a goal (§2.8)."""

    model_config = ConfigDict(frozen=True)

    budget: Budget = Field(default_factory=Budget)
    time: str | None = Field(default=None, description="Free-form deadline / wall-clock constraint")
    environment: str | None = Field(default=None, description="Assumed execution environment")
    tools: list[str] = Field(default_factory=list, description="Tools assumed available")


class Goal(BaseModel):
    """A typed planning request.

    ``approval_ttl`` is expressed in seconds; ``None`` (the default) means
    the approval never expires (∞ per §2.8).
    """

    model_config = ConfigDict(frozen=True)

    id: str = Field(min_length=1)
    description: str = Field(min_length=1)
    constraints: Constraints = Field(default_factory=Constraints)
    risk_tolerance: RiskTolerance = RiskTolerance.BALANCED
    replan_policy: ReplanPolicy = ReplanPolicy.PATCH
    approval_ttl: timedelta | None = Field(
        default=None, description="Approval expiry, None = never"
    )
    metadata: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode="after")
    def _require_description(self) -> Goal:
        """Reject whitespace-only descriptions (a blank goal plans nothing)."""
        if not self.description.strip():
            raise ValueError("goal description must not be blank")
        return self
