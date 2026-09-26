"""Policy pack and evaluation request models (D4)."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from hiveplane.core.decision import ActionClass, DataSensitivity, DecisionOutcome
from hiveplane.core.run import AdmissionContext
from hiveplane.core.tools import ToolTrustLevel
from hiveplane.tenancy.context import DEFAULT_TENANT_ID


class PolicyPackDefaults(BaseModel):
    """Default behaviors a team pack requests."""

    model_config = ConfigDict(extra="forbid")

    sandbox_for_destructive: bool = True
    injection_scan: bool = True
    max_bytes: int | None = Field(default=None, gt=0)


class PolicyPackRule(BaseModel):
    """A single tighten-only rule inside a pack override."""

    model_config = ConfigDict(extra="forbid")

    action: DecisionOutcome
    action_class: ActionClass | None = None
    tool_trust: ToolTrustLevel | None = None


class PolicyPackRuleMatch(BaseModel):
    """Context a pack override applies to."""

    model_config = ConfigDict(extra="forbid")

    environment: AdmissionContext | None = None
    data_sensitivity: DataSensitivity | None = None


class PolicyPackOverride(BaseModel):
    """A context match and the rules applied when it matches."""

    model_config = ConfigDict(extra="forbid")

    match: PolicyPackRuleMatch
    rules: list[PolicyPackRule] = Field(min_length=1)


class PolicyPackSpec(BaseModel):
    """The spec block of a policy pack."""

    model_config = ConfigDict(extra="forbid")

    defaults: PolicyPackDefaults = Field(default_factory=PolicyPackDefaults)
    overrides: list[PolicyPackOverride] = Field(default_factory=list)
    approval_contacts: dict[str, str] = Field(default_factory=dict)
    inherits: list[str] = Field(default_factory=list)


class PolicyPackMetadata(BaseModel):
    """Policy pack identity."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1)
    team: str = Field(min_length=1)
    version: str = Field(min_length=1)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)


class PolicyPack(BaseModel):
    """A versioned, distributable policy bundle for a team."""

    model_config = ConfigDict(extra="forbid", populate_by_name=True)

    api_version: Literal["hiveplane/v1"] = Field(default="hiveplane/v1", alias="apiVersion")
    kind: Literal["PolicyPack"] = "PolicyPack"
    metadata: PolicyPackMetadata
    spec: PolicyPackSpec


class PolicyEvaluationRequest(BaseModel):
    """Request body for evaluating policy against a context."""

    model_config = ConfigDict(extra="forbid")

    run_id: str = Field(min_length=1)
    workload: str = Field(min_length=1)
    team: str | None = None
    environment: AdmissionContext
    tool_id: str | None = None
    tool_trust: ToolTrustLevel | None = None
    action_class: ActionClass | None = None
    data_sensitivity: DataSensitivity = DataSensitivity.INTERNAL
    budget_exhausted: bool = False
    taint_untrusted: bool = False
    dry_run: bool = False
