"""Reporting, compliance, retention, and purge models (M57, D38)."""

from __future__ import annotations

from datetime import date
from enum import StrEnum

from pydantic import AwareDatetime, BaseModel, ConfigDict, Field

from hiveplane.cost.depth import RoiReport
from hiveplane.cost.models import ShowbackReport
from hiveplane.delivery.models import DeliveryChannel
from hiveplane.fleet.cost import CostPeriodKind
from hiveplane.health.analytics import ApprovalReport
from hiveplane.tenancy.context import DEFAULT_TENANT_ID


class ReportKind(StrEnum):
    """The kind of artifact a report run produced."""

    DIGEST = "digest"
    EVIDENCE = "evidence"
    AUDIT_EXPORT = "audit_export"
    RETENTION = "retention"
    PURGE = "purge"


class ReportRun(BaseModel):
    """One generated report and a reference to its stored output."""

    model_config = ConfigDict(extra="forbid")

    report_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    kind: ReportKind
    period_key: str = Field(min_length=1, max_length=64)
    output_ref: str = Field(min_length=1, max_length=512)
    generated_at: AwareDatetime


class ReportSchedule(BaseModel):
    """A per-tenant digest schedule and its delivery channels."""

    model_config = ConfigDict(extra="forbid")

    schedule_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    team_id: str | None = Field(default=None, max_length=64)
    cron: str = Field(min_length=1, max_length=128)
    period_kind: CostPeriodKind = CostPeriodKind.WEEK
    channels: list[DeliveryChannel] = Field(default_factory=list)
    active: bool = True
    next_run: AwareDatetime | None = None
    created_at: AwareDatetime


class AuditExportFormat(StrEnum):
    """The serialization format of an audit export."""

    CSV = "csv"
    JSON = "json"


class IntegrityProof(BaseModel):
    """A Merkle-root-plus-chain-head proof over an exported audit range."""

    model_config = ConfigDict(extra="forbid")

    algorithm: str = Field(default="sha256", min_length=1, max_length=32)
    merkle_root: str = Field(min_length=1, max_length=64)
    leaf_count: int = Field(ge=0)
    first_sequence: int | None = None
    last_sequence: int | None = None
    chain_head: str = Field(min_length=1, max_length=64)


class AuditExport(BaseModel):
    """A tamper-evident export of the audit log for a period."""

    model_config = ConfigDict(extra="forbid")

    export_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    period_start: AwareDatetime
    period_end: AwareDatetime
    format: AuditExportFormat
    record_count: int = Field(ge=0)
    integrity_proof: IntegrityProof
    content: str
    created_at: AwareDatetime


class EvidenceFile(BaseModel):
    """One file inside a signed compliance evidence pack."""

    model_config = ConfigDict(extra="forbid")

    name: str = Field(min_length=1, max_length=253)
    media_type: str = Field(min_length=1, max_length=128)
    digest: str = Field(min_length=1, max_length=64)
    content: str


class PackSignature(BaseModel):
    """An Ed25519 signature over a pack or certificate digest."""

    model_config = ConfigDict(extra="forbid")

    key_id: str = Field(min_length=1, max_length=253)
    algorithm: str = Field(default="ed25519", min_length=1, max_length=32)
    digest: str = Field(min_length=1, max_length=512)
    signature: str = Field(min_length=1, max_length=1024)


class EvidencePack(BaseModel):
    """A signed, self-contained compliance bundle for a period."""

    model_config = ConfigDict(extra="forbid")

    pack_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    period_start: AwareDatetime
    period_end: AwareDatetime
    files: list[EvidenceFile] = Field(default_factory=list)
    signature: PackSignature
    created_at: AwareDatetime


class RetentionDataClass(StrEnum):
    """The tenant data classes governed by retention policies."""

    RUNS = "runs"
    LOGS = "logs"
    ARTIFACTS = "artifacts"
    AUDIT = "audit"
    METERING = "metering"


class StorePurgeCount(BaseModel):
    """The number of rows/objects deleted from one store."""

    model_config = ConfigDict(extra="forbid")

    store: str = Field(min_length=1, max_length=64)
    deleted: int = Field(ge=0)


class RetentionPurgeResult(BaseModel):
    """The outcome of a scheduled retention purge."""

    model_config = ConfigDict(extra="forbid")

    classes: list[StorePurgeCount] = Field(default_factory=list)
    completed_at: AwareDatetime


class PurgeCertificate(BaseModel):
    """A signed assertion that a tenant purge completed."""

    model_config = ConfigDict(extra="forbid")

    purge_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    scope: str = Field(min_length=1, max_length=64)
    stores: list[StorePurgeCount] = Field(default_factory=list)
    total_deleted: int = Field(ge=0)
    completed_at: AwareDatetime
    verifier: str = Field(min_length=1, max_length=253)
    signature: PackSignature


class PurgeRecord(BaseModel):
    """A persisted purged tenant with its certificate."""

    model_config = ConfigDict(extra="forbid")

    purge_id: str = Field(min_length=1, max_length=64)
    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    scope: str = Field(min_length=1, max_length=64)
    certificate: PurgeCertificate
    completed_at: AwareDatetime


class Redaction(BaseModel):
    """A count of redactions applied for one PII kind."""

    model_config = ConfigDict(extra="forbid")

    kind: str = Field(min_length=1, max_length=64)
    count: int = Field(ge=0)


class ScrubResult(BaseModel):
    """The result of scrubbing PII from a text value."""

    model_config = ConfigDict(extra="forbid")

    text: str
    redactions: list[Redaction] = Field(default_factory=list)


class DriftDigest(BaseModel):
    """The drift and quarantine summary inside a fleet digest."""

    model_config = ConfigDict(extra="forbid")

    quarantined: int = Field(ge=0)
    reinstated: int = Field(ge=0)
    active: int = Field(ge=0)
    drifting: list[str] = Field(default_factory=list)


class DigestContent(BaseModel):
    """A rendered fleet digest for one tenant and period (M57-01)."""

    model_config = ConfigDict(extra="forbid")

    tenant_id: str = Field(default=DEFAULT_TENANT_ID, min_length=1, max_length=64)
    period_key: str = Field(min_length=1, max_length=64)
    period_start: date
    period_end: date
    generated_at: AwareDatetime
    spend: ShowbackReport
    roi: RoiReport
    drift: DriftDigest
    approvals: ApprovalReport
