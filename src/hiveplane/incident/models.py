"""Domain models for incident mode (M53, D36)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field


class HaltScope(StrEnum):
    """The breadth of a fleet halt."""

    FLEET = "fleet"
    TENANT = "tenant"
    WORKLOAD = "workload"


class IncidentRecord(BaseModel):
    """A single incident-mode halt and (eventually) its recovery.

    ``resumed_at`` is ``None`` while the halt is active. ``owners_notified``
    records the fan-out targets that accepted the broadcast.
    """

    model_config = ConfigDict(extra="forbid")

    incident_id: str = Field(min_length=1, max_length=64)
    scope: HaltScope = HaltScope.FLEET
    scope_ref: str | None = Field(default=None, max_length=64)
    trigger: str = Field(default="operator", min_length=1, max_length=64)
    reason: str | None = Field(default=None, max_length=2000)
    actor: str = Field(min_length=1, max_length=253)
    halted_at: AwareDatetime
    resumed_at: AwareDatetime | None = None
    resumed_by: str | None = Field(default=None, max_length=253)
    owners_notified: list[str] = Field(default_factory=list)

    @property
    def active(self) -> bool:
        """Return True while the halt has not been resumed."""
        return self.resumed_at is None
