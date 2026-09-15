"""Trigger rule models (PRD 05: triggers)."""

from __future__ import annotations

from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator


class TriggerType(StrEnum):
    """Kinds of external events that can auto-start a run."""

    WEBHOOK = "webhook"
    ALERT = "alert"
    GITHUB_PR = "github_pr"
    CRON = "cron"


class TriggerMode(StrEnum):
    """How a trigger operates over time."""

    ONE_SHOT = "one-shot"
    WATCH = "watch"
    SCHEDULED = "scheduled"


class TriggerMatch(BaseModel):
    """Event-matching criteria shared by trigger types."""

    model_config = ConfigDict(extra="forbid")

    severity: list[str] = Field(default_factory=list)
    service: str | None = None
    paths: list[str] = Field(default_factory=list)

    def has_criteria(self) -> bool:
        """Return True when at least one match criterion is set."""
        return bool(self.severity or self.service or self.paths)


class TriggerRule(BaseModel):
    """A rule that matches an external event to this workload."""

    model_config = ConfigDict(extra="forbid")

    type: TriggerType
    url: str | None = None
    match: TriggerMatch | None = None
    source: str | None = None
    events: list[str] = Field(default_factory=list)
    schedule: str | None = None
    mode: TriggerMode | None = None
    max_concurrent: int | None = Field(default=None, ge=1)

    @model_validator(mode="after")
    def _check_type_requirements(self) -> TriggerRule:
        if self.type is TriggerType.WEBHOOK and not self.url:
            raise ValueError("webhook trigger requires 'url'")
        if self.type is TriggerType.ALERT and not self.source:
            raise ValueError("alert trigger requires 'source'")
        if self.type is TriggerType.GITHUB_PR and not self.events:
            raise ValueError("github_pr trigger requires 'events'")
        if self.type is TriggerType.CRON and not self.schedule:
            raise ValueError("cron trigger requires 'schedule'")
        if self.type is not TriggerType.CRON and (
            self.match is None or not self.match.has_criteria()
        ):
            raise ValueError(
                "trigger requires at least one match criterion (or a schedule for cron)"
            )
        return self
