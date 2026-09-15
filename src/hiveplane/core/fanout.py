"""Result fan-out models (PRD 05: result delivery, D15)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class FanOutType(StrEnum):
    """Supported result-delivery destination types."""

    SLACK = "slack"
    TEAMS = "teams"
    JIRA = "jira"
    WEBHOOK = "webhook"


class FanOutDestination(BaseModel):
    """A single result-delivery destination."""

    model_config = ConfigDict(extra="forbid")

    type: FanOutType
    channel: str | None = None
    url: str | None = None
    project: str | None = None
    issue_type: str | None = None

    @model_validator(mode="after")
    def _check_type_requirements(self) -> FanOutDestination:
        if self.type in (FanOutType.SLACK, FanOutType.TEAMS) and not self.channel:
            raise ValueError(f"{self.type.value} destination requires 'channel'")
        if self.type is FanOutType.JIRA and not (self.project and self.issue_type):
            raise ValueError("jira destination requires 'project' and 'issue_type'")
        if self.type is FanOutType.WEBHOOK and not self.url:
            raise ValueError("webhook destination requires 'url'")
        return self


class FanOutSpec(BaseModel):
    """Destinations notified on each terminal outcome."""

    model_config = ConfigDict(extra="forbid")

    on_completed: list[FanOutDestination] = Field(default_factory=list)
    on_failed: list[FanOutDestination] = Field(default_factory=list)
    on_escalation: list[FanOutDestination] = Field(default_factory=list)
    always_include: list[str] = Field(default_factory=list)
