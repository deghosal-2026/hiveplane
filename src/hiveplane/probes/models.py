"""Typed records for synthetic probes (M43)."""

from __future__ import annotations

from enum import StrEnum
from typing import Literal

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

from hiveplane.tenancy.context import DEFAULT_TENANT_ID


class ProbeStatus(StrEnum):
    """Outcome of a probe run."""

    PASSED = "passed"
    FAILED = "failed"


class ProbeSpec(BaseModel):
    """A scheduled ping-task with a known-good expected behavior."""

    model_config = ConfigDict(extra="forbid")

    workload_id: str = Field(min_length=1)
    input: dict[str, JsonValue] = Field(default_factory=dict)
    expected: JsonValue | None = None
    interval_seconds: int = Field(default=300, gt=0)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)


class ProbeOutcome(BaseModel):
    """The raw result a probe runner produces."""

    model_config = ConfigDict(extra="forbid")

    passed: bool
    latency_ms: int = Field(default=0, ge=0)
    cost_usd: float = Field(default=0.0, ge=0.0)
    detail: dict[str, JsonValue] = Field(default_factory=dict)


class ProbeWarning(BaseModel):
    """An early drift warning raised by a failing probe (M43-02)."""

    model_config = ConfigDict(extra="forbid")

    workload_id: str = Field(min_length=1)
    reason: str = Field(min_length=1)
    rule_id: str = "probe.decay"
    created_at: AwareDatetime


class ProbeResult(BaseModel):
    """A recorded probe run (tagged ``probe`` so it never counts as production)."""

    model_config = ConfigDict(extra="forbid")

    probe_id: str = Field(min_length=1)
    workload_id: str = Field(min_length=1)
    passed: bool
    status: ProbeStatus
    latency_ms: int = Field(ge=0)
    cost_usd: float = Field(ge=0.0)
    detail: dict[str, JsonValue] = Field(default_factory=dict)
    tag: Literal["probe"] = "probe"
    warning: ProbeWarning | None = None
    created_at: AwareDatetime
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)


class ProbeSchedule(BaseModel):
    """The next scheduled probe run for a workload."""

    model_config = ConfigDict(extra="forbid")

    workload_id: str = Field(min_length=1)
    interval_seconds: int = Field(gt=0)
    next_run: AwareDatetime
    last_run: AwareDatetime | None = None
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
