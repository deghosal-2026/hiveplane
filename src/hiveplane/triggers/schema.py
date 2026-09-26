"""The strict trigger DSL (M27-01, D23).

A trigger is a typed document; unknown fields are errors, not ignored. The DSL
is the operator contract: a source, a typed event filter, a target
workload/pipeline, an injection-safe task template, dedup/cooldown/rate-limit
controls, an admission rule, and — for cron/watch — a validated timezone-aware
schedule with a missed-schedule policy.
"""

from __future__ import annotations

from enum import StrEnum
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from pydantic import (
    AliasChoices,
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    field_validator,
    model_validator,
)

from hiveplane.fleet.triggers import TriggerSource, TriggerTargetKind
from hiveplane.tenancy.context import DEFAULT_TENANT_ID
from hiveplane.triggers.cron import CronError, CronExpression
from hiveplane.triggers.templating import TemplateError, validate_template


class AdmissionRule(StrEnum):
    """How a matched event is admitted once accepted."""

    STAGING_AUTO = "staging-auto"
    GATED = "gated"
    DENY = "deny"


class MissedSchedulePolicy(StrEnum):
    """What to do when a scheduled window was missed."""

    SKIP = "skip"
    CATCH_UP = "catch_up"
    CATCH_UP_ALL = "catch_up_all"


class TriggerTarget(BaseModel):
    """What a trigger fires: a workload or a pipeline."""

    model_config = ConfigDict(extra="forbid")

    kind: TriggerTargetKind
    ref: str = Field(min_length=1, max_length=253)


class TriggerFilter(BaseModel):
    """Structured event-matching criteria."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    events: list[str] = Field(
        default_factory=list,
        validation_alias=AliasChoices("event", "events"),
    )
    actions: list[str] = Field(default_factory=list)
    repo: str | None = Field(default=None, max_length=253)
    service: str | None = Field(default=None, max_length=253)

    def is_empty(self) -> bool:
        """Return True when no criterion is set."""
        return not (self.events or self.actions or self.repo or self.service)


class DedupSpec(BaseModel):
    """Collapse duplicate events sharing a rendered key within a window."""

    model_config = ConfigDict(extra="forbid")

    key: str | None = Field(default=None, max_length=512)
    window_minutes: int = Field(default=30, gt=0, le=10080)

    @field_validator("key")
    @classmethod
    def _key_is_a_template(cls, value: str | None) -> str | None:
        if value is not None:
            _require_template(value, "dedup.key")
        return value


class RateLimitSpec(BaseModel):
    """A per-trigger token bucket."""

    model_config = ConfigDict(extra="forbid")

    max_per_minute: int = Field(gt=0, le=100000)
    burst: int = Field(default=0, ge=0, le=100000)


class TriggerSpec(BaseModel):
    """The operator-facing trigger declaration."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    source: TriggerSource
    target: TriggerTarget
    filter: TriggerFilter = Field(default_factory=TriggerFilter)
    task_template: dict[str, str] = Field(default_factory=dict)
    dedup: DedupSpec | None = None
    cooldown_seconds: int = Field(default=0, ge=0, le=86400)
    rate_limit: RateLimitSpec | None = None
    admission_rule: AdmissionRule = AdmissionRule.GATED
    timezone: str = Field(default="UTC", min_length=1, max_length=64)
    schedule: str | None = Field(default=None, max_length=128)
    missed_schedule_policy: MissedSchedulePolicy = MissedSchedulePolicy.SKIP
    max_concurrent_runs: int | None = Field(default=None, ge=1, le=1000)
    max_field_bytes: int = Field(default=8192, gt=0, le=1048576)
    max_total_bytes: int = Field(default=65536, gt=0, le=10485760)
    enabled: bool = True
    created_at: AwareDatetime | None = None

    @field_validator("timezone")
    @classmethod
    def _timezone_is_known(cls, value: str) -> str:
        try:
            ZoneInfo(value)
        except (ZoneInfoNotFoundError, ValueError) as exc:
            raise ValueError(f"unknown timezone {value!r}") from exc
        return value

    @field_validator("task_template")
    @classmethod
    def _templates_are_safe(cls, value: dict[str, str]) -> dict[str, str]:
        for field_name, template in value.items():
            if not field_name:
                raise ValueError("task_template field names must be non-empty")
            _require_template(template, f"task_template.{field_name}")
        return value

    @model_validator(mode="after")
    def _schedule_requirements(self) -> TriggerSpec:
        scheduled = self.source in (TriggerSource.CRON, TriggerSource.WATCH)
        if scheduled and not self.schedule:
            raise ValueError(f"{self.source.value} triggers require 'schedule'")
        if not scheduled and self.schedule is not None:
            raise ValueError("'schedule' is only valid for cron and watch triggers")
        if self.schedule is not None:
            try:
                CronExpression.parse(self.schedule)
            except CronError as exc:
                raise ValueError(f"invalid cron schedule: {exc}") from exc
        return self


def _require_template(template: str, where: str) -> None:
    try:
        validate_template(template)
    except TemplateError as exc:
        raise ValueError(f"{where}: {exc}") from exc
