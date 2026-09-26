"""Desired-state reconciliation models (M25-09, D21/D22).

A desired spec is one declared fleet object (workload, policy, trigger, or
budget) from a git or API source at a revision. Reconcile state tracks the last
observed/desired hash and reconcile time per source. A drift record captures a
field-level difference between declared and observed state with its resolution.
"""

from __future__ import annotations

from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field, JsonValue

from hiveplane.tenancy.context import DEFAULT_TENANT_ID


class SpecSource(StrEnum):
    """Where desired state is declared."""

    GIT = "git"
    API = "api"


class SpecKind(StrEnum):
    """The kind of fleet object a desired spec declares."""

    WORKLOAD = "workload"
    POLICY = "policy"
    TRIGGER = "trigger"
    BUDGET = "budget"


class ReconcileStatus(StrEnum):
    """The convergence status of a reconciliation source."""

    PENDING = "pending"
    IN_SYNC = "in_sync"
    DRIFTED = "drifted"
    ERROR = "error"


class DriftResolution(StrEnum):
    """How a drift record was resolved."""

    UNRESOLVED = "unresolved"
    DECLARED_WINS = "declared_wins"
    OBSERVED_WINS = "observed_wins"
    IGNORED = "ignored"


class DesiredSpec(BaseModel):
    """One declared fleet object from a desired-state source."""

    model_config = ConfigDict(extra="forbid")

    spec_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    source_id: str = Field(min_length=1, max_length=128)
    source: SpecSource
    kind: SpecKind
    name: str = Field(min_length=1, max_length=253)
    revision: str = Field(min_length=1, max_length=128)
    content_hash: str = Field(min_length=1, max_length=128)
    spec: dict[str, JsonValue] = Field(default_factory=dict)
    updated_at: AwareDatetime


class ReconcileState(BaseModel):
    """The last-observed convergence state of a source."""

    model_config = ConfigDict(extra="forbid")

    source_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    source: SpecSource
    last_revision: str = Field(min_length=1, max_length=128)
    last_observed_hash: str = Field(min_length=1, max_length=128)
    last_reconcile_at: AwareDatetime | None = None
    status: ReconcileStatus = ReconcileStatus.PENDING


class DriftRecord(BaseModel):
    """A field-level difference between declared and observed state."""

    model_config = ConfigDict(extra="forbid")

    drift_id: str = Field(min_length=1, max_length=128)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    object_ref: str = Field(min_length=1, max_length=253)
    field: str = Field(min_length=1, max_length=253)
    desired_value: JsonValue = None
    observed_value: JsonValue = None
    detected_at: AwareDatetime
    resolution: DriftResolution = DriftResolution.UNRESOLVED
    reconcile_run_id: str | None = Field(default=None, max_length=128)
