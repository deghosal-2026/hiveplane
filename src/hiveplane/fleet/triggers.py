"""Trigger models (M25-02, D21/D23).

A trigger binds an external event source to a workload or pipeline: a structured
event filter, a payload-to-task mapping, dedup/cooldown/rate-limit controls, and
an admission rule. History is append-only (events, runs, DLQ).
"""

from __future__ import annotations

from enum import StrEnum
from typing import Self

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue, model_validator

from hiveplane.tenancy.context import DEFAULT_TENANT_ID


class TriggerSource(StrEnum):
    """Where a trigger's events originate."""

    WEBHOOK = "webhook"
    GITHUB = "github"
    ALERTMANAGER = "alertmanager"
    CRON = "cron"
    WATCH = "watch"


class TriggerTargetKind(StrEnum):
    """What a trigger fires."""

    WORKLOAD = "workload"
    PIPELINE = "pipeline"


class AdmissionRule(StrEnum):
    """How an event is admitted once accepted."""

    AUTO = "auto"
    APPROVAL_GATED = "approval_gated"
    DENY = "deny"


class MissedSchedulePolicy(StrEnum):
    """What to do when a cron window was missed."""

    SKIP = "skip"
    RUN_ONCE = "run_once"
    CATCH_UP = "catch_up"


class TriggerOutcome(StrEnum):
    """The disposition of a received trigger event."""

    ACCEPTED = "accepted"
    DEDUPLICATED = "deduplicated"
    SUPPRESSED_COOLDOWN = "suppressed_cooldown"
    SUPPRESSED_FREEZE = "suppressed_freeze"
    REJECTED_RATE = "rejected_rate"
    REJECTED_BACKPRESSURE = "rejected_backpressure"
    REJECTED_SIGNATURE = "rejected_signature"
    SKIPPED_CONCURRENT = "skipped_concurrent"
    FAILED = "failed"


class TriggerRunStatus(StrEnum):
    """The outcome of a trigger's attempt to fire a run."""

    SUBMITTED = "submitted"
    BLOCKED_CERT = "blocked_cert"
    BLOCKED_ADMISSION = "blocked_admission"
    FAILED = "failed"


class EventFilter(BaseModel):
    """Structured filter deciding whether an event matches a trigger."""

    model_config = ConfigDict(extra="forbid")

    service: str | None = Field(default=None, max_length=253)
    events: list[str] = Field(default_factory=list)
    expression: str | None = Field(default=None, max_length=2000)


class DedupConfig(BaseModel):
    """How duplicate events are collapsed."""

    model_config = ConfigDict(extra="forbid")

    key_template: str = Field(min_length=1, max_length=512)
    window_seconds: int = Field(default=300, gt=0)


class RateLimit(BaseModel):
    """A token-bucket rate limit for a trigger."""

    model_config = ConfigDict(extra="forbid")

    max_events: int = Field(gt=0)
    window_seconds: int = Field(gt=0)


class TaskTemplate(BaseModel):
    """Maps event payload paths onto task fields."""

    model_config = ConfigDict(extra="forbid")

    mapping: dict[str, str] = Field(min_length=1)


class Trigger(BaseModel):
    """A declared trigger for a workload or pipeline."""

    model_config = ConfigDict(extra="forbid")

    trigger_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    source: TriggerSource
    target_kind: TriggerTargetKind
    target_ref: str = Field(min_length=1, max_length=253)
    event_filter: EventFilter = Field(default_factory=EventFilter)
    task_template: TaskTemplate
    dedup_config: DedupConfig
    cooldown_seconds: int = Field(default=0, ge=0)
    rate_limit: RateLimit | None = None
    admission_rule: AdmissionRule = AdmissionRule.AUTO
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    schedule: str | None = Field(default=None, max_length=128)
    missed_schedule_policy: MissedSchedulePolicy = MissedSchedulePolicy.SKIP
    enabled: bool = True
    created_at: AwareDatetime

    @model_validator(mode="after")
    def _source_requirements(self) -> Self:
        if self.source is TriggerSource.CRON and not self.schedule:
            raise ValueError("cron triggers require 'schedule'")
        if self.source is not TriggerSource.CRON and self.schedule is not None:
            raise ValueError("'schedule' is only valid for cron triggers")
        return self


class TriggerEvent(BaseModel):
    """An append-only record of a received trigger event."""

    model_config = ConfigDict(extra="forbid")

    event_id: str = Field(min_length=1, max_length=128)
    trigger_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    source: TriggerSource
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    received_at: AwareDatetime
    dedup_key: str | None = Field(default=None, max_length=512)
    outcome: TriggerOutcome
    reason: str | None = Field(default=None, max_length=2000)


class TriggerRun(BaseModel):
    """The decision and run linkage produced by an accepted event."""

    model_config = ConfigDict(extra="forbid")

    trigger_id: str = Field(min_length=1, max_length=64)
    event_id: str = Field(min_length=1, max_length=128)
    run_id: str | None = Field(default=None, max_length=64)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    fired_at: AwareDatetime
    status: TriggerRunStatus
    reason: str | None = Field(default=None, max_length=2000)


class TriggerDlqEntry(BaseModel):
    """A dead-lettered trigger event awaiting replay."""

    model_config = ConfigDict(extra="forbid")

    entry_id: str = Field(min_length=1, max_length=128)
    trigger_id: str = Field(min_length=1, max_length=64)
    event_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    payload: dict[str, JsonValue] = Field(default_factory=dict)
    headers: dict[str, str] = Field(default_factory=dict)
    failure_reason: str = Field(min_length=1, max_length=2000)
    attempts: int = Field(default=0, ge=0)
    created_at: AwareDatetime
    replayed_at: AwareDatetime | None = None
