"""Registry records: workloads, versions, tools, triggers, and summaries."""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

from hiveplane.certification.models import CertificationStatus
from hiveplane.core.decision import ActionClass
from hiveplane.core.run import AdmissionContext as AdmissionContext
from hiveplane.core.spec import RuntimeAdapter
from hiveplane.core.tools import ToolTrustLevel
from hiveplane.core.triggers import TriggerRule
from hiveplane.core.workload import AgentWorkload
from hiveplane.tenancy.context import DEFAULT_TENANT_ID
from hiveplane.transparency.provenance import WorkloadBundle


class VersionStatus(StrEnum):
    """Lifecycle of a stored manifest version."""

    ACTIVE = "active"
    PENDING = "pending"
    SUPERSEDED = "superseded"


class WorkloadRecord(BaseModel):
    """The current registered state of a workload."""

    model_config = ConfigDict(extra="forbid")

    name: str
    manifest: AgentWorkload
    current_version: int = Field(ge=1)
    certification_status: CertificationStatus
    owner: str
    team: str | None = None
    runtime: RuntimeAdapter
    created_at: AwareDatetime
    updated_at: AwareDatetime
    needs_re_certification: bool = False
    artifact_hash: str | None = None
    bundle: WorkloadBundle | None = None
    production_runs_survived: int = Field(default=0, ge=0)
    last_run_at: AwareDatetime | None = None
    failure_count: int = Field(default=0, ge=0)
    last_failure_at: AwareDatetime | None = None
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)


class WorkloadVersion(BaseModel):
    """An append-only manifest version record."""

    model_config = ConfigDict(extra="forbid")

    workload: str
    version: int = Field(ge=1)
    manifest: AgentWorkload
    status: VersionStatus = VersionStatus.ACTIVE
    created_at: AwareDatetime
    re_certification_required: bool = False
    changed_fields: list[str] = Field(default_factory=list)


class ToolRecord(BaseModel):
    """A registered MCP tool definition."""

    model_config = ConfigDict(extra="forbid")

    tool_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    mcp_server: str = Field(min_length=1)
    trust_level: ToolTrustLevel
    description: str | None = None
    parameters_schema: dict[str, JsonValue] = Field(default_factory=dict)
    registered_at: AwareDatetime
    registered_by: str = Field(min_length=1)


class TriggerRecord(BaseModel):
    """A trigger rule stored for a workload."""

    model_config = ConfigDict(extra="forbid")

    trigger_id: str = Field(min_length=1)
    workload: str = Field(min_length=1)
    rule: TriggerRule
    created_at: AwareDatetime


class EnforcementSummary(BaseModel):
    """What the control plane would enforce for a manifest, without persisting."""

    model_config = ConfigDict(extra="forbid")

    valid: bool
    name: str
    owner: str
    team: str | None = None
    runtime: RuntimeAdapter
    certification_status: CertificationStatus
    production_admitted: bool
    required_status_for_production: CertificationStatus = CertificationStatus.CERTIFIED
    tools_allowed: list[str] = Field(default_factory=list)
    tools_denied: list[str] = Field(default_factory=list)
    approvals_required_for: list[ActionClass] = Field(default_factory=list)
    budget_per_run_usd: float
    budget_per_day_usd: float
    sandbox_enabled: bool
    output_shaping_enabled: bool
    trigger_count: int = Field(default=0, ge=0)
    fan_out_destinations: int = Field(default=0, ge=0)
    warnings: list[str] = Field(default_factory=list)


class AdmissionDecision(BaseModel):
    """The result of an admission check for a target context."""

    model_config = ConfigDict(extra="forbid")

    workload: str
    context: AdmissionContext
    admitted: bool
    required_status: CertificationStatus | None = None
    actual_status: CertificationStatus
    reason: str | None = None


class CatalogEntry(BaseModel):
    """A lightweight fleet-catalog entry for listing views."""

    model_config = ConfigDict(extra="forbid")

    name: str
    owner: str
    team: str | None = None
    runtime: RuntimeAdapter
    certification_status: CertificationStatus
    current_version: int = Field(ge=1)
    updated_at: AwareDatetime
    last_run_at: AwareDatetime | None = None
    failure_count: int = Field(default=0, ge=0)
    last_failure_at: AwareDatetime | None = None


class VersionDiff(BaseModel):
    """The field-level difference between two manifest versions."""

    model_config = ConfigDict(extra="forbid")

    workload: str
    from_version: int = Field(ge=1)
    to_version: int = Field(ge=1)
    changed_fields: list[str] = Field(default_factory=list)
    re_certification_required: bool = False


class ToolRegistration(BaseModel):
    """Request body for onboarding an MCP tool."""

    model_config = ConfigDict(extra="forbid")

    tool_id: str = Field(min_length=1)
    name: str = Field(min_length=1)
    mcp_server: str = Field(min_length=1)
    trust_level: ToolTrustLevel
    description: str | None = None
    parameters_schema: dict[str, JsonValue] = Field(default_factory=dict)


class PromoteRequest(BaseModel):
    """Request body for promoting a manifest version."""

    model_config = ConfigDict(extra="forbid")

    version: int = Field(ge=1)
