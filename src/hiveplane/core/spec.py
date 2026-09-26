"""Workload manifest spec models (D1, DD-01).

The spec is the stable contract between the control plane and adapters. It
composes the runtime, model, budget, approvals, certification, triggers,
sandbox, output shaping, tools, fan-out, health, and observability blocks.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import UTC, datetime
from enum import StrEnum
from typing import Any

from pydantic import (
    AwareDatetime,
    BaseModel,
    ConfigDict,
    Field,
    JsonValue,
    model_validator,
)

from hiveplane.certification.models import CertificationStatus
from hiveplane.core.decision import ActionClass
from hiveplane.core.fanout import FanOutSpec
from hiveplane.core.health import HealthSpec
from hiveplane.core.sandbox import SandboxSpec
from hiveplane.core.shaping import OutputShapingSpec
from hiveplane.core.tools import ToolsSpec
from hiveplane.core.triggers import TriggerRule
from hiveplane.core.types import Duration


class RuntimeAdapter(StrEnum):
    """Supported runtime adapter types in v0.1.0."""

    RAW_WORKER = "raw-worker"
    LANGGRAPH = "langgraph"


class RuntimeSpec(BaseModel):
    """Adapter type and entrypoint for a workload."""

    model_config = ConfigDict(extra="forbid")

    adapter: RuntimeAdapter
    entrypoint: str = Field(min_length=1)
    env: dict[str, str] = Field(default_factory=dict)


class ModelStrategy(StrEnum):
    """How models are selected for a workload."""

    FIXED = "fixed"
    TIERED = "tiered"
    ROUTER = "router"


class ModelIdentity(BaseModel):
    """The exact model identity a certification binds to (DD-10)."""

    model_config = ConfigDict(extra="forbid")

    provider: str = Field(min_length=1)
    family: str = Field(min_length=1)
    version: str = Field(min_length=1)


def canonical_model_identity(identity: ModelIdentity) -> str:
    """Return the canonical ``provider/family/version`` model identity string."""
    return f"{identity.provider}/{identity.family}/{identity.version}"


def validate_model_identity(value: str) -> str:
    """Validate a canonical ``provider/family/version`` model identity string.

    Raises:
        ValueError: when the value is not three non-empty slash-separated parts.
    """
    parts = value.split("/")
    if len(parts) != 3 or any(not part.strip() for part in parts):
        raise ValueError(
            "model_identity must be 'provider/family/version' "
            f"(e.g. 'openai/gpt-4o/2024-08-06'), got {value!r}"
        )
    return value


class ModelSpec(BaseModel):
    """Model strategy and the certification-bound model identity."""

    model_config = ConfigDict(extra="forbid")

    strategy: ModelStrategy = ModelStrategy.TIERED
    identity: ModelIdentity | None = None


class BudgetSpec(BaseModel):
    """Per-run, per-day, and per-team spend ceilings (DD-04)."""

    model_config = ConfigDict(extra="forbid")

    per_run_usd: float = Field(gt=0.0)
    per_day_usd: float = Field(gt=0.0)
    per_team_usd: float | None = None

    @model_validator(mode="after")
    def _check_ordering(self) -> BudgetSpec:
        if self.per_day_usd < self.per_run_usd:
            raise ValueError("per_day_usd must be >= per_run_usd")
        if self.per_team_usd is not None and self.per_team_usd < self.per_day_usd:
            raise ValueError("per_team_usd must be >= per_day_usd")
        return self


class ApprovalsSpec(BaseModel):
    """Which action classes require human approval."""

    model_config = ConfigDict(extra="forbid")

    required_for: list[ActionClass] = Field(default_factory=list)
    contact: str | None = None
    auto_escalate_after: Duration = 300


class ObservabilitySpec(BaseModel):
    """Telemetry contract and trace sampling configuration (DD-06)."""

    model_config = ConfigDict(extra="forbid")

    contract: str = "standard"
    trace_sampling: float = Field(default=1.0, gt=0.0, le=1.0)


class IOSpec(BaseModel):
    """Declared input/output schemas for pipeline handoff validation (M29-03)."""

    model_config = ConfigDict(extra="forbid")

    input_schema: dict[str, JsonValue] | None = None
    output_schema: dict[str, JsonValue] | None = None


def parse_io_spec(data: Mapping[str, Any] | None) -> IOSpec | None:
    """Validate a manifest ``spec.io`` block, or return None when absent."""
    if data is None:
        return None
    return IOSpec.model_validate(dict(data))


class CertificationSpec(BaseModel):
    """Benchmark reference, thresholds, and current certification status."""

    model_config = ConfigDict(extra="forbid")

    benchmark_corpus: str | None = None
    staging_threshold: float = Field(default=0.80, ge=0.0, le=1.0)
    production_threshold: float = Field(default=0.90, ge=0.0, le=1.0)
    no_critical_failures: bool = True
    latency_budget_ms: int = Field(default=30000, gt=0)
    re_cert_interval: Duration = 14 * 86400
    status: CertificationStatus = CertificationStatus.UNCERTIFIED
    attestation_id: str | None = None
    certified_at: AwareDatetime | None = None
    certified_by: str | None = None
    expires_at: AwareDatetime | None = None

    @model_validator(mode="after")
    def _check_thresholds_and_status(self) -> CertificationSpec:
        if self.production_threshold < self.staging_threshold:
            raise ValueError("production_threshold must be >= staging_threshold")
        if self.status is CertificationStatus.CERTIFIED:
            if not self.attestation_id:
                raise ValueError("certified status requires attestation_id")
            if self.expires_at is None:
                raise ValueError("certified status requires expires_at")
            if self.expires_at <= datetime.now(UTC):
                raise ValueError("certified status requires expires_at in the future")
        return self


class WorkloadSpec(BaseModel):
    """The complete specification block of an agent workload manifest."""

    model_config = ConfigDict(extra="forbid")

    runtime: RuntimeSpec
    budget: BudgetSpec
    model: ModelSpec = Field(default_factory=ModelSpec)
    certification: CertificationSpec | None = None
    triggers: list[TriggerRule] = Field(default_factory=list)
    sandbox: SandboxSpec | None = None
    output_shaping: OutputShapingSpec | None = None
    tools: ToolsSpec = Field(default_factory=ToolsSpec)
    approvals: ApprovalsSpec = Field(default_factory=ApprovalsSpec)
    fan_out: FanOutSpec = Field(default_factory=FanOutSpec)
    health: HealthSpec = Field(default_factory=HealthSpec)
    observability: ObservabilitySpec = Field(default_factory=ObservabilitySpec)
    io: IOSpec | None = None

    @model_validator(mode="after")
    def _certification_requires_model_identity(self) -> WorkloadSpec:
        if self.certification is not None and self.model.identity is None:
            raise ValueError("spec.model.identity is required when spec.certification is present")
        return self
